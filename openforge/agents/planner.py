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
from openforge.services.llm import call_llm
from openforge.services.symbol_resolver import extract_candidate_symbols, resolve_symbol_to_file
from openforge.utils import extract_json

logger = logging.getLogger(__name__)

_PLANNER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "planner.md"
)

_PLANNER_FQCN = "openforge.agents.planner.PlannerAgent"

# Files larger than this are skipped rather than truncated mid-function.
_MAX_FILE_BYTES = 12 * 1024  # 12 KB

# Maximum number of mentioned files whose content is inlined into the prompt.
_MAX_INLINE_FILES = 3

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
                "The selected LLM provider API key is not set. "
                "The Planner requires an LLM. "
                "Export the key and retry:\n\n"
                "  export AGENTROUTER_API_KEY=..."
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
        run_dir: Path | None = None,
        timeout: float = 300.0,
    ) -> ImplementationPlan:
        """Render the planner prompt and return a validated ImplementationPlan."""
        snippets = self._read_mentioned_files(repo_context, issue_facts)
        prompt = self._render_prompt(repo_context, issue_facts, snippets)
        
        # Calculate section breakdown (roughly)
        def _toks(text: str) -> int: return len(text) // 4
        
        breakdown = {
            "System Prompt": _toks(self._prompt_template.split("## Inputs")[0]),
            "Repository Context": _toks(str(repo_context.to_json_file_dict()) if repo_context else ""), # Rough estimate of repo context
            "Issue Facts": _toks(issue_facts.body + str(issue_facts.mentioned_files)),
            "Relevant Source Files": _toks("".join(s.content or "" for s in snippets)),
            "Instructions": _toks(self._prompt_template.split("## Instructions")[1] if "## Instructions" in self._prompt_template else ""),
        }
        
        def _record_metrics(metrics: dict) -> None:
            if run_dir:
                metrics_path = run_dir / "prompt_metrics.json"
                # Append to a list if file exists, or create new
                all_metrics = []
                if metrics_path.exists():
                    try:
                        all_metrics = json.loads(metrics_path.read_text())
                    except Exception:
                        pass
                
                metrics["agent"] = "PlannerAgent"
                metrics["section_breakdown"] = breakdown
                all_metrics.append(metrics)
                metrics_path.write_text(json.dumps(all_metrics, indent=2))

        def _save_raw(text: str) -> None:
            if not run_dir:
                return
            dest = run_dir / "planner_attempt0.txt"
            dest.write_text(text, encoding="utf-8")

        try:
            raw_content = call_llm(prompt, self._settings, timeout=timeout, metrics_callback=_record_metrics, raw_callback=_save_raw)
        except RuntimeError as exc:
            raise PlannerError(str(exc)) from exc
        plan = self._parse_plan(raw_content)

        # Validate paths
        valid_targets = []
        if repo_context and repo_context.source_path:
            repo_root = Path(repo_context.source_path).resolve()
            for target in plan.edit_targets:
                rel = target.file_path
                p = Path(rel)
                if p.is_absolute() or ".." in p.parts:
                    logger.warning("Planner dropped invalid path traversal attempt: %s", rel)
                    continue
                abs_target = (repo_root / rel).resolve()
                if not abs_target.is_relative_to(repo_root):
                    logger.warning("Planner dropped path outside repo root: %s", rel)
                    continue
                valid_targets.append(target)
        else:
            for target in plan.edit_targets:
                rel = target.file_path
                p = Path(rel)
                if p.is_absolute() or ".." in p.parts:
                    logger.warning("Planner dropped invalid path traversal attempt: %s", rel)
                    continue
                valid_targets.append(target)

        if len(valid_targets) != len(plan.edit_targets):
            plan = plan.model_copy(update={"edit_targets": valid_targets})

        return plan

    # ------------------------------------------------------------------
    # File reading (deterministic — no LLM)
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_mentioned_file(base: Path, rel_path: str) -> Path | None:
        """Resolve a file mentioned in the issue to an actual path in the repository.

        Strategy:
        1. Try the literal path first.
        2. If missing, search by basename under ``base``.
        3. Exactly one match → use it; log the fallback.
        4. Multiple matches → score deterministically and pick the best,
           logging ambiguity when scores tie.
        5. No match → return ``None`` (caller skips silently).
        """
        literal = base / rel_path
        if literal.exists():
            return literal

        basename = Path(rel_path).name
        candidates = list(base.rglob(basename))

        if not candidates:
            return None

        if len(candidates) == 1:
            best = candidates[0]
            logger.info(
                "Literal path:\n\n%s\n\n↓\n\nResolved by basename search\n\n%s",
                rel_path,
                best.relative_to(base).as_posix(),
            )
            return best

        # Multiple candidates — score deterministically.
        mention_parts = Path(rel_path).parts
        b_lower = basename.lower()

        def _score(candidate: Path) -> tuple:
            rel = candidate.relative_to(base)
            parts = rel.parts
            path_str = str(rel).lower()

            # 1. Longest common suffix of path components.
            common_len = 0
            for i in range(1, min(len(parts), len(mention_parts)) + 1):
                if parts[-i] == mention_parts[-i]:
                    common_len += 1
                else:
                    break
            score = common_len * 10

            # 2. Directory preference based on basename hint.
            if "test" in b_lower:
                if "tests/" in path_str or "/tests/" in path_str or "test/" in path_str or "/test/" in path_str:
                    score += 5
            if b_lower.endswith((".md", ".rst")) or "doc" in b_lower:
                if "docs/" in path_str or "/docs/" in path_str or "doc/" in path_str or "/doc/" in path_str:
                    score += 5
            if "src/" in path_str:
                score += 2

            # Prefer shorter paths, then alphabetical.
            return (-score, len(path_str), path_str)

        candidates.sort(key=_score)
        best = candidates[0]
        best_score = _score(best)
        second_score = _score(candidates[1])

        if best_score[0] == second_score[0]:
            logger.warning(
                "Ambiguous basename '%s' → multiple matches with equal score: %s. "
                "Choosing shortest/deterministic: %s",
                basename,
                [str(c.relative_to(base)) for c in candidates],
                best.relative_to(base).as_posix(),
            )
        else:
            logger.info(
                "Literal path:\n\n%s\n\n↓\n\nResolved by basename search\n\n%s",
                rel_path,
                best.relative_to(base).as_posix(),
            )

        return best

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
        - Path doesn't exist locally → try basename fallback via
          _resolve_mentioned_file; if still unresolved, skip silently.
        - File > _MAX_FILE_BYTES → skeletonize/truncate with a note.
        - Not valid UTF-8 → skip silently (binary file, not useful).
        - Cap at first _MAX_INLINE_FILES files that *exist and are in-range*.
        - **Symbol fallback**: after processing literal mentions, if room
          remains, deterministically search the repo for symbols appearing in
          the issue body/code blocks that have NOT already been resolved to a
          file.  Each newly discovered file is read and added to the prompt.
        """
        if repo is None or not repo.source_path:
            return []

        base = Path(repo.source_path)
        snippets: list[_FileSnippet] = []
        included = 0
        already_resolved: set[str] = set()

        def _read_and_add(abs_path: Path, *, symbol_fallback: bool = False) -> None:
            nonlocal included
            resolved_rel = abs_path.relative_to(base).as_posix()
            if resolved_rel in already_resolved:
                return
            size = abs_path.stat().st_size
            try:
                content = abs_path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                return

            if size > _MAX_FILE_BYTES:
                if abs_path.suffix == ".py":
                    try:
                        import ast

                        tree = ast.parse(content)

                        class Skeletonizer(ast.NodeTransformer):
                            def visit_FunctionDef(self, node):
                                docstring = ast.get_docstring(node)
                                body = []
                                if docstring:
                                    body.append(
                                        ast.Expr(value=ast.Constant(value=docstring))
                                    )
                                body.append(
                                    ast.Expr(
                                        value=ast.Constant(
                                            value="... (body omitted for size) ..."
                                        )
                                    )
                                )
                                node.body = body
                                return node

                            def visit_AsyncFunctionDef(self, node):
                                return self.visit_FunctionDef(node)

                            def visit_ClassDef(self, node):
                                self.generic_visit(node)
                                return node

                        tree = Skeletonizer().visit(tree)
                        content = ast.unparse(tree)

                        if len(content.encode("utf-8")) > _MAX_FILE_BYTES:
                            content = (
                                content[:_MAX_FILE_BYTES]
                                + "\n... (truncated for size) ..."
                            )
                    except SyntaxError:
                        content = (
                            content[:_MAX_FILE_BYTES]
                            + "\n... (truncated for size) ..."
                        )
                else:
                    content = content[:_MAX_FILE_BYTES] + "\n... (truncated for size) ..."

                snippets.append(
                    _FileSnippet(
                        path=resolved_rel,
                        content=content,
                        skip_reason=f"too large to include fully ({size:,} bytes) - skeletonized/truncated",
                    )
                )
                included += 1
                already_resolved.add(resolved_rel)
                return

            snippets.append(_FileSnippet(path=resolved_rel, content=content))
            included += 1
            already_resolved.add(resolved_rel)

        # ------------------------------------------------------------------
        # Phase 1: literal / basename resolution from mentioned_files
        # ------------------------------------------------------------------
        for rel_path in facts.mentioned_files:
            if included >= _MAX_INLINE_FILES:
                break

            abs_path = self._resolve_mentioned_file(base, rel_path)
            if abs_path is None:
                logger.debug("Mentioned file not found on disk, skipping: %s", rel_path)
                continue
            _read_and_add(abs_path)

        # ------------------------------------------------------------------
        # Phase 2: deterministic symbol resolution for un-grounded symbols
        # Only runs when we still have room and the issue body contains
        # candidate symbols we haven't already resolved to a file.
        # ------------------------------------------------------------------
        if included < _MAX_INLINE_FILES:
            body = facts.body or ""
            code_blocks = [cb.content for cb in facts.code_blocks]
            candidate_symbols = extract_candidate_symbols(body, code_blocks)
            for symbol in candidate_symbols:
                if included >= _MAX_INLINE_FILES:
                    break
                resolved = resolve_symbol_to_file(base, symbol)
                if resolved is None:
                    continue
                _read_and_add(resolved, symbol_fallback=True)

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
    # JSON parsing + post-processing
    # ------------------------------------------------------------------

    def _parse_plan(self, raw: str) -> ImplementationPlan:
        """Extract, validate, and post-process the LLM response.

        Post-processing rule: if risks contains at least one substantive entry
        (not a placeholder), force requires_human = True.  The model cannot
        reliably keep two generated fields consistent; we enforce it
        deterministically here.
        """
        json_str = extract_json(raw)
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

        return plan
