"""Shared patch detection logic.

The Coder stages changes with ``git add`` but does not commit them.
Every consumer that needs to inspect the generated patch must use
``git diff --staged`` — never ``git diff HEAD~1``, which fails on
shallow clones (``--depth 1``) that have only one commit.

This module is the single source of truth for computing generated patches.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PatchResult:
    """Outcome of inspecting a repository's staged changes."""

    diff: str
    files_modified: int
    diff_hash: str

    @property
    def is_empty(self) -> bool:
        return self.files_modified == 0 or not self.diff.strip()


def get_staged_diff(repo_path: str | Path) -> str:
    """Return the staged unified diff for *repo_path*.

    Uses ``git diff --staged`` which reflects exactly what the Coder
    produces after ``git add``.  Works on shallow clones.
    """
    result = subprocess.run(
        ["git", "diff", "--staged"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        logger.warning(
            "git diff --staged failed in %s (exit %d): %s",
            repo_path,
            result.returncode,
            result.stderr.strip(),
        )
    return result.stdout


def count_files_modified(diff: str) -> int:
    """Count the number of files changed in a unified diff."""
    if not diff:
        return 0
    return len(diff.split("diff --git")) - 1


def get_patch(repo_path: str | Path) -> PatchResult:
    """Compute the generated patch for a repository.

    This is the single entry-point that all consumers should use.
    Returns a ``PatchResult`` with the diff text, file count, and SHA-256 hash.
    """
    diff = get_staged_diff(repo_path)
    files = count_files_modified(diff)
    h = hashlib.sha256(diff.encode("utf-8")).hexdigest() if diff else ""
    result = PatchResult(diff=diff, files_modified=files, diff_hash=h)
    logger.info(
        "Patch: %d files modified, %d bytes, hash=%s",
        result.files_modified,
        len(result.diff),
        result.diff_hash[:12] if result.diff_hash else "(empty)",
    )
    return result


def load_code_json(code_json_path: str | Path) -> dict:
    """Load and return the ``data`` section of a ``code.json`` artifact.

    Returns an empty dict if the file is missing or malformed.
    """
    path = Path(code_json_path)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text()).get("data", {})
    except (json.JSONDecodeError, OSError):
        return {}


def evaluate_test_results(code_json_path: str | Path) -> tuple[bool, list[dict]]:
    """Evaluate structured test results from ``code.json``.

    Returns ``(all_passed, test_results_list)``.
    If ``code.json`` is missing or has no test results, returns
    ``(False, [])`` so that missing data is never treated as success.
    """
    data = load_code_json(code_json_path)
    results = data.get("test_results", [])
    if not results:
        return False, []
    all_passed = all(t.get("passed", False) for t in results)
    return all_passed, results
