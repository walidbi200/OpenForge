"""CodingAgent — Milestone 5.

Reads prompts/coder.md, renders it with the current file contents +
ImplementationPlan, calls OpenRouter, writes the returned file contents to
disk, creates a git branch, and runs the test suite once.

Design constraints (from ARCHITECTURE.md):
- The LLM may only touch paths that appear in plan.files_to_modify.
- Full file content is requested, not a diff — diffs from LLMs are
  unreliable; git does the diffing itself.
- Deterministic git operations: branch creation, diff capture, unexpected-
  file detection.  None of this involves the LLM.
- Run tests once. No retry loop.
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from openforge.config import Settings
from openforge.models import ImplementationPlan, RepositoryContext
from openforge.services.openrouter import call_llm
from openforge.utils import extract_json

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
_CODER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "coder.md"
)

# Same guard philosophy as the Planner — skip rather than silently truncate.
# Coder reads files to rewrite them whole, so the ceiling is higher than
# the Planner's 20 KB context limit.  100 KB is a reasonable upper bound
# for a single source file that can be safely sent and rewritten.
_MAX_FILE_BYTES = 100 * 1024  # 100 KB

_ALLOWED_TEST_RUNNERS = {"pytest", "python", "npm", "pnpm", "yarn", "bun", "cargo", "go"}

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class FileEdit:
    """One file returned by the LLM."""

    path: str
    action: str  # "modify" | "create"
    new_content: str


@dataclass
class TestResult:
    """Outcome of running plan.tests_to_run."""

    command: str
    returncode: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.returncode == 0


@dataclass
class CodeResult:
    """Everything produced by CodingAgent.implement()."""

    branch_name: str
    edits: list[FileEdit] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)  # wrote but not in plan
    git_diff: str = ""
    test_results: list[TestResult] = field(default_factory=list)
    summary: str = ""

    def to_json_file_dict(self) -> dict:
        return {
            "branch_name": self.branch_name,
            "edits": [
                {"path": e.path, "action": e.action} for e in self.edits
            ],
            "unexpected_files": self.unexpected_files,
            "git_diff": self.git_diff,
            "test_results": [
                {
                    "command": t.command,
                    "returncode": t.returncode,
                    "passed": t.passed,
                    "stdout": t.stdout[:4000],
                    "stderr": t.stderr[:2000],
                }
                for t in self.test_results
            ],
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CoderError(RuntimeError):
    """Raised when CodingAgent cannot complete its work."""


# ---------------------------------------------------------------------------
# Pure helpers (same pattern as planner.py)
# ---------------------------------------------------------------------------


def _safe_join(repo_root: Path, rel: str) -> Path:
    """Safely join a relative path to a repo root, preventing traversal out of bounds."""
    if Path(rel).is_absolute():
        raise CoderError(f"Absolute paths are not allowed: {rel!r}")
    if ".." in Path(rel).parts:
        raise CoderError(f"Parent directory traversal ('..') is not allowed: {rel!r}")
    target = (repo_root / rel).resolve()
    root = repo_root.resolve()
    if not target.is_relative_to(root):
        raise CoderError(f"Refusing to write outside the repo: {rel!r}")
    return target


def _slugify(text: str, max_len: int = 30) -> str:
    """Turn arbitrary text into a short branch-name-safe slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


def _branch_name(issue_number: int | None, goal: str) -> str:
    slug = _slugify(goal) or "change"
    if issue_number:
        return f"openforge/{issue_number}-{slug}"
    return f"openforge/{slug}"


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------


class CodingAgent:
    """Implements a plan on disk: writes code, creates a branch, runs tests."""

    def __init__(self, settings: Settings) -> None:
        if not settings.llm_enabled:
            raise CoderError(
                "OPENROUTER_API_KEY is not set. "
                "The CodingAgent requires an LLM. "
                "Export the key and retry:\n\n"
                "  export OPENROUTER_API_KEY=sk-or-..."
            )
        self._settings = settings
        self._prompt_template = self._load_prompt()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def implement(
        self,
        repo_context: RepositoryContext,
        plan: ImplementationPlan,
        issue_number: int | None = None,
        *,
        timeout: float = 120.0,
    ) -> CodeResult:
        """Full pipeline: branch → LLM write → git operations → tests."""
        repo_path = Path(repo_context.source_path)
        branch = _branch_name(issue_number, plan.goal)

        # 0. Validate plan.files_to_modify up front (fail fast)
        for rel in plan.files_to_modify:
            _safe_join(repo_path, rel)

        # 1. Create and check out a new branch — deterministic, no LLM.
        self._git_create_branch(repo_path, branch)

        # 2. Read current file contents fresh from disk.
        current_files = self._read_files(repo_path, plan.files_to_modify)

        # 3. Build prompt and call LLM.
        prompt = self._render_prompt(repo_context, plan, current_files)
        try:
            raw = call_llm(
                prompt,
                self._settings,
                timeout=timeout,
                max_tokens=16000,
            )
        except RuntimeError as exc:
            raise CoderError(str(exc)) from exc
        logger.info("LLM returned %d bytes", len(raw))

        # 4. Parse LLM response.
        logger.info("Parsing LLM response...")
        edits = self._parse_response(raw, plan.files_to_modify)
        logger.info("Parsed %d edits", len(edits))

        # 5. Write files to disk — only paths in plan.files_to_modify.
        unexpected: list[str] = []
        written: list[FileEdit] = []
        plan_set = set(plan.files_to_modify)
        for edit in edits:
            if edit.path not in plan_set:
                logger.warning(
                    "CodingAgent: LLM returned a file outside plan.files_to_modify"
                    " — SKIPPING: %s",
                    edit.path,
                )
                unexpected.append(edit.path)
                continue
            target = _safe_join(repo_path, edit.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(edit.new_content, encoding="utf-8")
            written.append(edit)
            logger.info("Wrote %s (%d bytes)", edit.path, len(edit.new_content))

        # Stage written files so creations show up in diff
        if written:
            subprocess.run(
                ["git", "add", "--"] + [e.path for e in written],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

        # 6. Capture git diff and detect actual changed files.
        logger.info("Running git diff...")
        git_diff = self._git_diff(repo_path, staged=True)
        logger.info("Running git diff --name-only...")
        changed_names = self._git_diff_name_only(repo_path, staged=True)

        # Flag files that git says changed but aren't in the plan.
        for changed in changed_names:
            if changed not in plan_set and changed not in unexpected:
                logger.warning(
                    "CodingAgent: git diff shows unexpected change to %s (not in plan)",
                    changed,
                )
                unexpected.append(changed)

        # 7. Run tests once — no retry.
        logger.info("Running tests...")
        test_results = self._run_tests(plan.tests_to_run, cwd=repo_path)
        logger.info("Finished running tests")

        return CodeResult(
            branch_name=branch,
            edits=written,
            unexpected_files=unexpected,
            git_diff=git_diff,
            test_results=test_results,
            summary=self._parse_summary(raw),
        )

    # ------------------------------------------------------------------
    # File reading
    # ------------------------------------------------------------------

    def _load_prompt(self) -> str:
        try:
            return _CODER_PROMPT_PATH.read_text()
        except FileNotFoundError:
            raise CoderError(
                f"Coder prompt not found at {_CODER_PROMPT_PATH}. "
                "This file must exist before running the coder."
            )

    def _read_files(
        self, base: Path, paths: list[str]
    ) -> dict[str, str | None]:
        """Read current content of each path.  None = file doesn't exist."""
        result: dict[str, str | None] = {}
        for rel in paths:
            abs_path = _safe_join(base, rel)
            if not abs_path.exists():
                result[rel] = None
                logger.debug("File does not exist (will be created): %s", rel)
                continue
            size = abs_path.stat().st_size
            if size > _MAX_FILE_BYTES:
                raise CoderError(
                    f"File {rel} is {size:,} bytes — exceeds the {_MAX_FILE_BYTES:,} B "
                    "limit for safe LLM inlining.  The plan should not ask for a "
                    "file this large to be rewritten wholesale."
                )
            try:
                result[rel] = abs_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raise CoderError(
                    f"File {rel} is not valid UTF-8 text and cannot be sent to the LLM."
                )
        return result

    # ------------------------------------------------------------------
    # Prompt rendering
    # ------------------------------------------------------------------

    def _render_prompt(
        self,
        repo: RepositoryContext,
        plan: ImplementationPlan,
        current_files: dict[str, str | None],
    ) -> str:
        file_blocks = self._render_file_blocks(current_files)
        steps_text = "\n".join(
            f"{i}. {step}" for i, step in enumerate(plan.implementation_steps, 1)
        )
        return self._prompt_template.format(
            language=repo.language or "unknown",
            framework=repo.framework or "unknown",
            test_command=repo.test_command or "unknown",
            goal=plan.goal,
            implementation_steps=steps_text,
            file_blocks=file_blocks,
        )

    @staticmethod
    def _render_file_blocks(current_files: dict[str, str | None]) -> str:
        parts: list[str] = []
        for path, content in current_files.items():
            if content is None:
                parts.append(
                    f"### {path}\n(File does not exist yet — action should be \"create\")"
                )
            else:
                parts.append(f"### {path}\n```\n{content}\n```")
        return "\n\n".join(parts)



    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str, allowed_paths: list[str]) -> list[FileEdit]:
        """Parse the LLM JSON response into a list of FileEdit objects."""
        json_str = extract_json(raw)
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError as exc:
            raise CoderError(
                f"Coder LLM returned non-JSON output.\n"
                f"Parse error: {exc}\n"
                f"Raw output (first 500 chars): {raw[:500]}"
            ) from exc

        files_raw = payload.get("files", [])
        if not isinstance(files_raw, list):
            raise CoderError(f"Expected 'files' to be a list in coder response; got: {type(files_raw)}")

        edits: list[FileEdit] = []
        for entry in files_raw:
            path = entry.get("path", "")
            action = entry.get("action", "modify")
            new_content = entry.get("new_content", "")
            if not path:
                logger.warning("CodingAgent: skipping entry with empty path")
                continue
            if not isinstance(new_content, str):
                raise CoderError(f"new_content for {path!r} must be a string, got {type(new_content)}")
            edits.append(FileEdit(path=path, action=action, new_content=new_content))

        return edits

    @staticmethod
    def _parse_summary(raw: str) -> str:
        """Extract the summary field from the raw LLM response, best-effort."""
        json_str = extract_json(raw)
        try:
            payload = json.loads(json_str)
            return str(payload.get("summary", ""))
        except (json.JSONDecodeError, AttributeError):
            return ""

    # ------------------------------------------------------------------
    # Deterministic git operations
    # ------------------------------------------------------------------

    def _git_create_branch(self, cwd: Path, branch_name: str) -> None:
        """Create and checkout a new branch."""
        # Ensure working tree is clean before branching
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if status.stdout.strip():
            logger.warning("Working tree not clean before creating branch %r", branch_name)

        logger.debug("Creating branch: %s", branch_name)
        result = subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise CoderError(
                f"Failed to create branch {branch_name!r}:\n{result.stderr.strip()}"
            )
        logger.debug("Checked out new branch: %s", branch_name)

    def _git_diff(self, repo_path: Path, staged: bool = False) -> str:
        """Capture full unified diff of all staged+unstaged changes."""
        args = ["git", "diff"]
        if staged:
            args.append("--staged")
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def _git_diff_name_only(self, repo_path: Path, staged: bool = False) -> list[str]:
        """Return list of files changed according to git diff."""
        args = ["git", "diff", "--name-only"]
        if staged:
            args.append("--staged")
        result = subprocess.run(
            args,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _run_tests(
        self, commands: list[str], cwd: Path
    ) -> list[TestResult]:
        """Run each test command once. Capture output. No retries."""
        results: list[TestResult] = []
        for cmd in commands:
            logger.debug("Running tests: %s", cmd)
            try:
                args = shlex.split(cmd)
            except ValueError as exc:
                logger.warning("Test command rejected (shlex parse error): %s — %s", cmd, exc)
                results.append(
                    TestResult(
                        command=cmd,
                        returncode=1,
                        stdout="",
                        stderr=f"REJECTED: Malformed command ({exc})",
                    )
                )
                continue

            if not args:
                continue

            if args[0] not in _ALLOWED_TEST_RUNNERS:
                logger.warning("Test command rejected (not in allowlist): %s", cmd)
                results.append(
                    TestResult(
                        command=cmd,
                        returncode=1,
                        stdout="",
                        stderr=f"REJECTED: Test runner '{args[0]}' is not in the allowlist ({', '.join(sorted(_ALLOWED_TEST_RUNNERS))}).",
                    )
                )
                continue

            try:
                proc = subprocess.run(
                    args,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=300,  # 5-minute hard cap
                )
                results.append(
                    TestResult(
                        command=cmd,
                        returncode=proc.returncode,
                        stdout=proc.stdout,
                        stderr=proc.stderr,
                    )
                )
                status = "PASSED" if proc.returncode == 0 else "FAILED"
                logger.debug("Test %s: %s (exit %d)", cmd, status, proc.returncode)
            except subprocess.TimeoutExpired:
                logger.warning("Test command timed out (300s): %s", cmd)
                results.append(
                    TestResult(
                        command=cmd,
                        returncode=1,
                        stdout="",
                        stderr=f"TIMEOUT: test command exceeded 300 seconds: {cmd}",
                    )
                )
            except FileNotFoundError as exc:
                logger.warning("Test command not found: %s — %s", cmd, exc)
                results.append(
                    TestResult(
                        command=cmd,
                        returncode=1,
                        stdout="",
                        stderr=f"COMMAND NOT FOUND: {exc}. Is the test runner installed in this environment?",
                    )
                )
        return results
