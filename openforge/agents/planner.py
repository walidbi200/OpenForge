"""PlannerAgent — Milestone 4.

Reads prompts/planner.md, renders it with RepositoryContext + IssueFacts
(including the actual source of mentioned files), calls OpenRouter, and
returns a validated ImplementationPlan.

Design constraints (from ARCHITECTURE.md):
- The LLM is used ONLY for semantic reasoning that cannot be deterministic.
- Every I/O contract is typed.
- Exit fast and clearly when the API key is absent — never silently degrade.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from openforge.config import Settings
from openforge.models import ImplementationPlan, IssueFacts, RepositoryContext

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_PLANNER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "planner.md"
)

_PLANNER_FQCN = "openforge.agents.planner.PlannerAgent"

# Files larger than this are skipped rather than truncated mid-function.
_MAX_FILE_BYTES = 20 * 1024  # 20 KB

# Maximum number of mentioned files whose content is inlined into the prompt.
_MAX_INLINE_FILES = 5

# A risk entry that is just filler / generic placeholder — not substantive.
# If every entry in risks[] matches this pattern we do NOT force requires_human.
_TRIVIAL_RISK_PATTERN = re.compile(
    r"^\s*(n/?a|none|no risks?|—|-)\s*$",
    re.IGNORECASE,
)


class PlannerError(RuntimeError):
    """Raised when the planner cannot produce a valid plan."""


# ---------------------------------------------------------------------------
# Internal data types
# ---------------------------------------------------------------------------


@dataclass
class _FileSnippet:
    """Result of attempting to read one mentioned file."""

    path: str
    content: str | None  # None → skipped
    skip_reason: str | None = None  # human-readable explanation when skipped


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_json(text: str) -> str:
    """Extract the first JSON object from a string that may contain prose.

    The model is instructed to return only JSON, but may occasionally wrap it
    in a markdown code fence.  This function handles both cases.
    """
    # Try a fenced ```json ... ``` block first.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)

    # Fall back to the first { ... } spanning the whole response.
    brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_match:
        return brace_match.group(1)

    return text  # let json.loads raise a descriptive error


def _is_trivial_risk(entry: str) -> bool:
    """Return True for placeholder/filler risk entries that carry no information."""
    return bool(_TRIVIAL_RISK_PATTERN.match(entry))


def _risks_are_substantive(risks: list[str]) -> bool:
    """True if at least one risk entry carries real information."""
    return bool(risks) and any(not _is_trivial_risk(r) for r in risks)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class PlannerAgent:
    """Calls an LLM to produce an ImplementationPlan from structured inputs."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_enabled:
            raise PlannerError(
                "OPENROUTER_API_KEY is not set. "
                "The Planner requires an LLM. "
                "Export the key and retry:\n\n"
                "  export OPENROUTER_API_KEY=sk-or-..."
            )
        self._settings = settings
        self._prompt_template = self._load_prompt()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(
        self,
        repo_context: RepositoryContext | None,
        issue_facts: IssueFacts,
        *,
        timeout: float = 60.0,
    ) -> ImplementationPlan:
        """Render the planner prompt and return a validated ImplementationPlan."""
        snippets = self._read_mentioned_files(repo_context, issue_facts)
        prompt = self._render_prompt(repo_context, issue_facts, snippets)
        raw_content = self._call_llm(prompt, timeout=timeout)
        plan = self._parse_plan(raw_content)

        # Validate paths
        valid_paths = []
        if repo_context and repo_context.source_path:
            repo_root = Path(repo_context.source_path).resolve()
            for rel in plan.files_to_modify:
                p = Path(rel)
                if p.is_absolute() or ".." in p.parts:
                    logger.warning("Planner dropped invalid path traversal attempt: %s", rel)
                    continue
                target = (repo_root / rel).resolve()
                if not target.is_relative_to(repo_root):
                    logger.warning("Planner dropped path outside repo root: %s", rel)
                    continue
                valid_paths.append(rel)
        else:
            for rel in plan.files_to_modify:
                p = Path(rel)
                if p.is_absolute() or ".." in p.parts:
                    logger.warning("Planner dropped invalid path traversal attempt: %s", rel)
                    continue
                valid_paths.append(rel)

        if valid_paths != plan.files_to_modify:
            plan = plan.model_copy(update={"files_to_modify": valid_paths})

        return plan

    # ------------------------------------------------------------------
    # File reading (deterministic — no LLM)
    # ------------------------------------------------------------------

    def _load_prompt(self) -> str:
        try:
            return _PLANNER_PROMPT_PATH.read_text()
        except FileNotFoundError:
            raise PlannerError(
                f"Planner prompt not found at {_PLANNER_PROMPT_PATH}. "
                "This file must exist before running the planner."
            )

    def _read_mentioned_files(
        self,
        repo: RepositoryContext | None,
        facts: IssueFacts,
    ) -> list[_FileSnippet]:
        """Read up to _MAX_INLINE_FILES files from mentioned_files.

        Rules (all deterministic):
        - No source_path → return empty list silently.
        - Path doesn't exist locally → skip silently.
        - File > _MAX_FILE_BYTES → skip with a note (no truncation).
        - Not valid UTF-8 → skip silently (binary file, not useful).
        - Cap at first _MAX_INLINE_FILES files that *exist and are in-range*.
        """
        if repo is None or not repo.source_path:
            return []

        base = Path(repo.source_path)
        snippets: list[_FileSnippet] = []
        included = 0

        for rel_path in facts.mentioned_files:
            if included >= _MAX_INLINE_FILES:
                break

            abs_path = base / rel_path

            if not abs_path.exists():
                logger.debug("Mentioned file not found on disk, skipping: %s", rel_path)
                continue

            size = abs_path.stat().st_size
            if size > _MAX_FILE_BYTES:
                snippets.append(
                    _FileSnippet(
                        path=rel_path,
                        content=None,
                        skip_reason=f"too large to include ({size:,} bytes)",
                    )
                )
                # A skipped-large file counts against the cap so we don't
                # enumerate the whole list looking for small files.
                included += 1
                logger.debug(
                    "Mentioned file too large (%d bytes), noting in prompt: %s",
                    size,
                    rel_path,
                )
                continue

            try:
                content = abs_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                logger.debug("Could not read mentioned file (binary/unreadable), skipping: %s", rel_path)
                continue

            snippets.append(_FileSnippet(path=rel_path, content=content))
            included += 1
            logger.debug("Included mentioned file in prompt (%d bytes): %s", size, rel_path)

        return snippets

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        repo: RepositoryContext | None,
        facts: IssueFacts,
        snippets: list[_FileSnippet],
    ) -> str:
        """Fill in the planner.md template with concrete values."""

        def _fmt(value: object) -> str:
            if value is None:
                return "unknown"
            if isinstance(value, list):
                return ", ".join(str(v) for v in value) if value else "—"
            return str(value)

        return self._prompt_template.format(
            # Repository context fields
            language=_fmt(repo.language if repo else None),
            framework=_fmt(repo.framework if repo else None),
            package_manager=_fmt(repo.package_manager if repo else None),
            test_command=_fmt(repo.test_command if repo else None),
            linter=_fmt(repo.linter if repo else None),
            ci=_fmt(repo.ci if repo else None),
            important_directories=_fmt(repo.important_directories if repo else None),
            # Issue facts fields
            issue_number=_fmt(facts.issue_number),
            issue_title=facts.issue_title,
            issue_type=facts.issue_type,
            has_traceback=str(facts.has_traceback).lower(),
            has_reproduction_steps=str(facts.has_reproduction_steps).lower(),
            mentioned_files=_fmt(facts.mentioned_files),
            checklist_items=_fmt(facts.checklist_items),
            issue_body=facts.body,
            # File contents section (new)
            file_contents=self._render_file_contents(snippets),
        )

    @staticmethod
    def _render_file_contents(snippets: list[_FileSnippet]) -> str:
        """Render the file-contents block to inject into the prompt."""
        if not snippets:
            return "(No mentioned files could be read from the local clone.)"

        parts: list[str] = []
        for s in snippets:
            if s.content is None:
                parts.append(f"### {s.path}\n[SKIPPED — {s.skip_reason}]")
            else:
                parts.append(
                    f"### {s.path}\n```\n{s.content}\n```"
                )
        return "\n\n".join(parts)

    # ------------------------------------------------------------------
    # LLM call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, *, timeout: float) -> str:
        """POST to OpenRouter and return the raw response text."""
        try:
            response = httpx.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {self._settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._settings.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except httpx.HTTPStatusError as exc:
            raise PlannerError(
                f"OpenRouter returned HTTP {exc.response.status_code}: "
                f"{exc.response.text[:300]}"
            ) from exc
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise PlannerError(f"OpenRouter call failed: {exc}") from exc

    # ------------------------------------------------------------------
    # JSON parsing + post-processing
    # ------------------------------------------------------------------

    def _parse_plan(self, raw: str) -> ImplementationPlan:
        """Extract, validate, and post-process the LLM response.

        Post-processing rule: if risks contains at least one substantive entry
        (not a placeholder), force requires_human = True.  The model cannot
        reliably keep two generated fields consistent; we enforce it
        deterministically here.
        """
        json_str = _extract_json(raw)
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise PlannerError(
                f"LLM returned non-JSON output.\n"
                f"Parse error: {exc}\n"
                f"Raw output (first 500 chars): {raw[:500]}"
            ) from exc

        try:
            plan = ImplementationPlan(**payload)
        except Exception as exc:
            raise PlannerError(
                f"LLM JSON does not match ImplementationPlan schema: {exc}\n"
                f"Payload: {json.dumps(payload, indent=2)[:500]}"
            ) from exc

        # Deterministic override: non-empty risks → requires_human must be True.
        if _risks_are_substantive(plan.risks) and not plan.requires_human:
            logger.debug(
                "requires_human override fired: risks=%r — forcing requires_human=True",
                plan.risks,
            )
            plan = plan.model_copy(update={"requires_human": True})

        return plan
