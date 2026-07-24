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
from openforge.services.llm import call_llm
from openforge.services.sandbox import get_sandbox
from openforge.utils import extract_json, repair_json

logger = logging.getLogger(__name__)

_CODER_PROMPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "prompts" / "coder.md"
)

# Coder uses SEARCH/REPLACE blocks, so it can handle larger files safely.
# 300 KB is a safe upper bound for modern LLMs to extract snippets from.
_MAX_FILE_BYTES = 300 * 1024  # 300 KB

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
        run_dir: Path | None = None,
        timeout: float = 300.0,
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
        
        def _toks(text: str) -> int: return len(text) // 4
        breakdown = {
            "System Prompt": _toks(self._prompt_template.split("## Context")[0]),
            "Repository Context": _toks(f"Language: {repo_context.language}\nFramework: {repo_context.framework}\nTest Command: {repo_context.test_command}"),
            "Implementation Plan": _toks(plan.goal + "".join(plan.implementation_steps)),
            "Relevant Source Files": _toks(self._render_file_blocks(current_files, plan.edit_targets)),
            "Instructions": _toks(self._prompt_template.split("Rules:")[1] if "Rules:" in self._prompt_template else ""),
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
                
                metrics["agent"] = "CodingAgent"
                metrics["section_breakdown"] = breakdown
                all_metrics.append(metrics)
                metrics_path.write_text(json.dumps(all_metrics, indent=2))

        raw = ""
        edits = []
        diagnostics = {}
        returned_paths = []
        
        for attempt in range(3):
            try:
                raw = call_llm(
                    prompt,
                    self._settings,
                    timeout=timeout,
                    metrics_callback=_record_metrics,
                    max_tokens=16000,
                )
            except RuntimeError as exc:
                raise CoderError(str(exc)) from exc
            logger.info("LLM returned %d bytes", len(raw))

            # Quick guard: if the response doesn't even start with '{',
            # it's almost certainly prose. Skip straight to repair+retry.
            stripped = raw.strip()
            if not stripped.startswith("{"):
                logger.warning(
                    "Response does not start with '{'; treating as prose. "
                    "Attempt %d/3.",
                    attempt + 1,
                )
                diagnostics = {
                    "status": "prose_detected",
                    "reason": "LLM returned conversational text instead of JSON.",
                    "hallucinated_paths": [],
                    "parser_warnings": ["Response did not start with '{'"],
                    "rejected_edits": [],
                    "skipped_files": [],
                }
                if attempt == 2:
                    logger.error("CodingAgent failed after 3 attempts (prose detected).")
                    return CodeResult(
                        branch_name=branch,
                        summary="Failed: LLM returned prose instead of JSON after 3 attempts.",
                    )
                # Inject a stricter JSON reminder for the next attempt
                prompt += (
                    "\n\nCRITICAL: Your previous response was prose, not valid JSON. "
                    "You MUST respond with ONLY a raw JSON object starting with '{' "
                    "and ending with '}'. No explanations. No thinking. No markdown. "
                    "ONLY JSON."
                )
                continue

            # 4. Parse LLM response.
            logger.info("Parsing LLM response...")
            edits, diagnostics = self._parse_response(raw, plan.files_to_modify, current_files)
            logger.info("Parsed %d edits", len(edits))

            # 5. Output Validation
            plan_set = set(plan.files_to_modify)
            returned_paths = []
            valid_edits = []
            
            for edit in edits:
                if edit.path not in plan_set:
                    logger.warning("Dropped hallucinated path not in plan: %s", edit.path)
                    continue
                if not edit.new_content.strip():
                    logger.warning("Dropped empty patch for file: %s", edit.path)
                    continue
                if edit.path in returned_paths:
                    logger.warning("Dropped duplicate edit for file: %s", edit.path)
                    continue
                
                returned_paths.append(edit.path)
                valid_edits.append(edit)
                
            edits = valid_edits

            if edits:
                break

            if attempt == 2:
                logger.error("CodingAgent failed to generate any valid edits after 3 attempts.")
                if run_dir:
                    debug_payload = {
                        "planner_output": plan.to_json_file_dict(),
                        "allowed_files": plan.files_to_modify,
                        "snippets_sent": list(current_files.keys()),
                        "raw_llm_response": raw,
                        "parser_diagnostics": diagnostics,
                    }
                    (run_dir / "coder_debug.json").write_text(json.dumps(debug_payload, indent=2))

                return CodeResult(branch_name=branch, summary="Failed to generate valid edits after 3 attempts.")

            # Retry prompt that is more forceful about JSON and exact file contents
            no_edit_warnings = json.dumps(diagnostics.get("parser_warnings", []), indent=2)
            prompt += (
                "\n\nYour previous attempt produced no valid edits. "
                "Diagnostics:\n"
                f"{no_edit_warnings}\n"
                "You MUST return a valid JSON object starting with '{' and ending with '}'. "
                "Do NOT write prose. Do NOT include markdown. ONLY raw JSON."
            )

        if not edits:
            logger.error("CodingAgent failed to generate any valid edits after retry.")
            if run_dir:
                debug_payload = {
                    "planner_output": plan.to_json_file_dict(),
                    "allowed_files": plan.files_to_modify,
                    "snippets_sent": list(current_files.keys()),
                    "raw_llm_response": raw,
                    "parser_diagnostics": diagnostics,
                }
                (run_dir / "coder_debug.json").write_text(json.dumps(debug_payload, indent=2))
            
            return CodeResult(branch_name=branch, summary="Failed to generate valid edits (empty patch).")

        missing_files = plan_set - set(returned_paths)
        if missing_files:
            logger.warning("LLM failed to return patches for planned files: %s", missing_files)

        # 6. Write files to disk
        written: list[FileEdit] = []
        unexpected: list[str] = []  # Keeping variable for return type compatibility
        for edit in edits:
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

        # Syntax Validation Loop
        for attempt in range(2):
            syntax_errors = self._validate_syntax(repo_path, changed_names)
            if not syntax_errors:
                break
                
            logger.warning("Syntax validation failed on attempt %d: %s", attempt + 1, syntax_errors)
            if attempt == 1:
                logger.error("Syntax errors not resolved after 2 attempts.")
                break
                
            # Request repair
            prompt = (
                f"You previously generated code that has Python syntax errors.\n"
                f"Fix the errors using a SEARCH/REPLACE block.\n\n"
                f"Errors:\n{syntax_errors}\n\n"
                f"Only return the fixed JSON block as described before."
            )
            try:
                repair_raw = call_llm(prompt, self._settings, timeout=timeout)
                repair_edits = self._parse_response(repair_raw, plan.files_to_modify, current_files)
                for edit in repair_edits:
                    target = _safe_join(repo_path, edit.path)
                    target.write_text(edit.new_content, encoding="utf-8")
                    subprocess.run(["git", "add", edit.path], cwd=repo_path, check=True)
            except Exception as e:
                logger.error("Syntax repair generation failed: %s", e)
                break

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
        test_results = self._run_tests(plan.tests_to_run, cwd=repo_path, repo_context=repo_context)
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
        file_blocks = self._render_file_blocks(current_files, plan.edit_targets)
        steps_text = "\n".join(
            f"{i}. {step}" for i, step in enumerate(plan.implementation_steps, 1)
        )
        # Use str.replace instead of str.format to avoid interpreting curly
        # braces inside source code as Python format placeholders.
        prompt = self._prompt_template
        prompt = prompt.replace("{language}", repo.language or "unknown")
        prompt = prompt.replace("{framework}", repo.framework or "unknown")
        prompt = prompt.replace("{test_command}", repo.test_command or "unknown")
        prompt = prompt.replace("{goal}", plan.goal)
        prompt = prompt.replace("{implementation_steps}", steps_text)
        prompt = prompt.replace("{file_blocks}", file_blocks)
        return prompt

    @staticmethod
    def _render_file_blocks(current_files: dict[str, str | None], edit_targets: list) -> str:
        parts: list[str] = []
        target_map = {t.file_path: t for t in edit_targets}
        
        for path, content in current_files.items():
            target = target_map.get(path)
            metadata = ""
            if target:
                metadata += f"Is Required: {target.is_required}\n"
                if target.expected_edit_locations:
                    metadata += f"Expected Edit Locations: {', '.join(target.expected_edit_locations)}\n"
                if target.expected_function_names:
                    metadata += f"Expected Function Names: {', '.join(target.expected_function_names)}\n"
                if target.expected_behavior_change:
                    metadata += f"Expected Behavior Change: {target.expected_behavior_change}\n"
                    
            if content is None:
                parts.append(
                    f"### {path}\n{metadata}(File does not exist yet — action should be \"create\")"
                )
            else:
                parts.append(f"### {path}\n{metadata}```\n{content}\n```")
        return "\n\n".join(parts)



    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str, allowed_paths: list[str], current_files: dict[str, str | None]) -> tuple[list[FileEdit], dict]:
        """Parse the LLM JSON response into a list of FileEdit objects and diagnostics."""
        json_str = extract_json(raw)
        try:
            payload = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("CodingAgent: JSON parsing failed. Attempting repair...")
            repaired_str = repair_json(json_str)
            try:
                payload = json.loads(repaired_str)
                logger.info("CodingAgent: JSON repair successful.")
            except json.JSONDecodeError as exc:
                Path("failed_coder_raw.json").write_text(raw, encoding="utf-8")
                Path("failed_coder_repaired.json").write_text(repaired_str, encoding="utf-8")
                raise CoderError(
                    f"Coder LLM returned non-JSON output.\n"
                    f"Parse error: {exc}\n"
                    f"Raw output saved to failed_coder_raw.json\n"
                    f"Raw output (first 500 chars): {raw[:500]}"
                ) from exc

        diagnostics = {
            "status": payload.get("status", "unknown"),
            "reason": payload.get("reason", ""),
            "hallucinated_paths": [],
            "parser_warnings": [],
            "rejected_edits": [],
            "skipped_files": [],
        }

        files_raw = payload.get("files", [])
        if not isinstance(files_raw, list):
            diagnostics["parser_warnings"].append(f"Expected 'files' to be a list; got: {type(files_raw)}")
            return [], diagnostics

        edits_by_path: dict[str, str] = {}
        
        for entry in files_raw:
            path = entry.get("path", "")
            action = entry.get("action", "modify")
            search = entry.get("search", "")
            replace = entry.get("replace", "")
            new_content_raw = entry.get("new_content", "")
            
            if not path:
                diagnostics["parser_warnings"].append("Skipping entry with empty path")
                continue
                
            if path not in allowed_paths:
                diagnostics["hallucinated_paths"].append(path)
                diagnostics["parser_warnings"].append(f"Path {path!r} not in allowed paths.")
                continue
                
            if action == "create":
                new_content = replace if replace else new_content_raw
                if not isinstance(new_content, str):
                    diagnostics["parser_warnings"].append(f"replace for {path!r} must be a string")
                    continue
                edits_by_path[path] = new_content
                current_files[path] = new_content
                continue
                
            # modify action
            if replace and search:
                # Apply SEARCH/REPLACE block
                original = current_files.get(path)
                if original is None:
                    diagnostics["parser_warnings"].append(f"File {path!r} does not exist but action is 'modify'")
                    continue
                
                if search not in original:
                    diagnostics["rejected_edits"].append({"path": path, "search": search})
                    diagnostics["parser_warnings"].append(f"Search block not found in {path!r}")
                    continue
                    
                new_content = original.replace(search, replace)
                current_files[path] = new_content
                edits_by_path[path] = new_content
            else:
                # Fallback to old format if LLM generated it
                if not isinstance(new_content_raw, str):
                    diagnostics["parser_warnings"].append(f"new_content for {path!r} must be a string")
                    continue
                current_files[path] = new_content_raw
                edits_by_path[path] = new_content_raw

        edits = [FileEdit(path=p, action="modify", new_content=c) for p, c in edits_by_path.items()]
        return edits, diagnostics

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
            logger.warning("Working tree not clean. Resetting and cleaning...")
            subprocess.run(["git", "reset", "--hard"], cwd=cwd, capture_output=True)
            subprocess.run(["git", "clean", "-fd"], cwd=cwd, capture_output=True)

        logger.debug("Creating/checking out branch: %s", branch_name)
        result = subprocess.run(
            ["git", "checkout", branch_name],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
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

    def _validate_syntax(self, repo_path: Path, changed_names: list[str]) -> str:
        """Run python -m py_compile on all changed .py files."""
        errors = []
        for changed in changed_names:
            if not changed.endswith(".py"):
                continue
            target = repo_path / changed
            if not target.exists():
                continue
            res = subprocess.run(
                ["python", "-m", "py_compile", str(target)],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                errors.append(res.stderr.strip())
        return "\n".join(errors)

    # ------------------------------------------------------------------
    # Test runner
    # ------------------------------------------------------------------

    def _run_tests(
        self, commands: list[str], cwd: Path, repo_context: RepositoryContext
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
                sandbox = get_sandbox()
                proc = sandbox.run(args, cwd=cwd)
                
                # Check for missing dependencies and auto-install
                if proc.returncode != 0:
                    import re
                    err_text = proc.stderr or proc.stdout
                    match = re.search(r"(?:ModuleNotFoundError|ImportError):\s*([^\n]+)", err_text)
                    if match:
                        pkg_manager = repo_context.package_manager or "pip"
                        if pkg_manager == "uv":
                            install_cmd = "uv sync"
                        elif pkg_manager == "poetry":
                            install_cmd = "poetry install"
                        elif pkg_manager == "pnpm":
                            install_cmd = "pnpm install"
                        elif pkg_manager == "yarn":
                            install_cmd = "yarn install"
                        elif pkg_manager == "npm":
                            install_cmd = "npm install"
                        else:
                            install_cmd = "pip install -e ."
                        
                        logger.info("Missing dependency detected: %s. Attempting to install via %s", match.group(1), install_cmd)
                        install_proc = subprocess.run(
                            shlex.split(install_cmd),
                            cwd=cwd,
                            capture_output=True,
                            text=True
                        )
                        if install_proc.returncode == 0:
                            logger.info("Dependencies installed successfully. Retrying test...")
                            proc = sandbox.run(args, cwd=cwd)
                        else:
                            logger.warning("Failed to auto-install dependencies. Test will remain failed.")
                            proc.stderr += f"\n\nMissing dependency: {match.group(1)}\nAuto-install failed ({install_cmd}):\n{install_proc.stderr}\n"

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
