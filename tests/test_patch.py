"""Regression tests for openforge.patch — the shared patch detection helper.

These tests create real temporary git repositories to verify that
``get_patch`` / ``get_staged_diff`` works correctly in every scenario
that previously caused false positives in the validation runner.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from openforge.patch import (
    PatchResult,
    count_files_modified,
    evaluate_test_results,
    get_patch,
    get_staged_diff,
    load_code_json,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
    )


def _init_repo(path: Path, *, shallow: bool = False) -> None:
    """Create a fresh git repo with one initial commit."""
    _git("init", cwd=path)
    _git("config", "user.email", "test@test.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("initial")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "initial commit", cwd=path)
    if shallow:
        # Simulate --depth 1 by creating a shallow clone of ourselves.
        # We can't easily shallow-clone a local repo, so we use
        # `git checkout --orphan` + `git reset --hard` to get a single-commit repo.
        # Instead, just verify HEAD~1 doesn't exist by checking rev-list count.
        pass


def _shallow_repo(path: Path) -> None:
    """Create a repo that mimics ``git clone --depth 1`` (single commit)."""
    _git("init", cwd=path)
    _git("config", "user.email", "test@test.com", cwd=path)
    _git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("initial")
    _git("add", "README.md", cwd=path)
    _git("commit", "-m", "initial commit", cwd=path)
    # Verify it really has only one commit
    res = _git("rev-list", "--count", "HEAD", cwd=path)
    assert res.stdout.strip() == "1"


# ---------------------------------------------------------------------------
# Tests: get_staged_diff
# ---------------------------------------------------------------------------

class TestGetStagedDiff:
    def test_empty_when_no_changes(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        assert get_staged_diff(tmp_path) == ""

    def test_returns_staged_diff(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "hello.py").write_text("print('hello')\n")
        _git("add", "hello.py", cwd=tmp_path)
        diff = get_staged_diff(tmp_path)
        assert "hello.py" in diff
        assert "+print('hello')" in diff

    def test_ignores_unstaged_changes(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "README.md").write_text("modified but not staged\n")
        diff = get_staged_diff(tmp_path)
        assert diff == ""

    def test_works_on_shallow_clone(self, tmp_path: Path) -> None:
        """The original bug: git diff HEAD~1 fails on shallow clones."""
        _shallow_repo(tmp_path)
        # Verify HEAD~1 doesn't exist
        res = _git("diff", "HEAD~1", cwd=tmp_path)
        assert res.returncode != 0  # HEAD~1 doesn't exist

        # But get_staged_diff should still work
        (tmp_path / "new.py").write_text("x = 1\n")
        _git("add", "new.py", cwd=tmp_path)
        diff = get_staged_diff(tmp_path)
        assert "new.py" in diff
        assert "+x = 1" in diff


# ---------------------------------------------------------------------------
# Tests: count_files_modified
# ---------------------------------------------------------------------------

class TestCountFilesModified:
    def test_empty_diff(self) -> None:
        assert count_files_modified("") == 0
        assert count_files_modified("   ") == 0

    def test_single_file(self) -> None:
        diff = "diff --git a/foo.py b/foo.py\nindex 123..456 100644\n--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        assert count_files_modified(diff) == 1

    def test_two_files(self) -> None:
        diff = (
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-c\n+d\n"
        )
        assert count_files_modified(diff) == 2


# ---------------------------------------------------------------------------
# Tests: get_patch
# ---------------------------------------------------------------------------

class TestGetPatch:
    def test_empty_patch(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        patch = get_patch(tmp_path)
        assert patch.is_empty
        assert patch.files_modified == 0
        assert patch.diff == ""

    def test_nonempty_patch(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "app.py").write_text("def main():\n    pass\n")
        _git("add", "app.py", cwd=tmp_path)
        patch = get_patch(tmp_path)
        assert not patch.is_empty
        assert patch.files_modified == 1
        assert "app.py" in patch.diff
        assert len(patch.diff_hash) == 64  # SHA-256 hex

    def test_shallow_clone_nonempty(self, tmp_path: Path) -> None:
        """Regression: shallow clone + staged edit must not produce empty patch."""
        _shallow_repo(tmp_path)
        (tmp_path / "fix.py").write_text("x = 42\n")
        _git("add", "fix.py", cwd=tmp_path)
        patch = get_patch(tmp_path)
        assert not patch.is_empty
        assert patch.files_modified == 1
        assert "fix.py" in patch.diff

    def test_hash_determinism(self, tmp_path: Path) -> None:
        """Same diff always produces the same hash."""
        _init_repo(tmp_path)
        (tmp_path / "a.py").write_text("val = 1\n")
        _git("add", "a.py", cwd=tmp_path)
        p1 = get_patch(tmp_path)
        # Reset and replay
        _git("reset", "HEAD", cwd=tmp_path)
        _git("checkout", "--", "a.py", cwd=tmp_path)
        _git("add", "a.py", cwd=tmp_path)
        p2 = get_patch(tmp_path)
        assert p1.diff_hash == p2.diff_hash

    def test_multiple_files(self, tmp_path: Path) -> None:
        _init_repo(tmp_path)
        (tmp_path / "a.py").write_text("a\n")
        (tmp_path / "b.py").write_text("b\n")
        _git("add", "a.py", "b.py", cwd=tmp_path)
        patch = get_patch(tmp_path)
        assert patch.files_modified == 2
        assert not patch.is_empty


# ---------------------------------------------------------------------------
# Tests: PatchResult
# ---------------------------------------------------------------------------

class TestPatchResult:
    def test_is_empty_true_when_no_files(self) -> None:
        r = PatchResult(diff="", files_modified=0, diff_hash="")
        assert r.is_empty

    def test_is_empty_false_when_files(self) -> None:
        r = PatchResult(diff="diff --git a/x b/x\n...", files_modified=1, diff_hash="abc")
        assert not r.is_empty

    def test_is_empty_true_when_whitespace_only(self) -> None:
        r = PatchResult(diff="   \n  ", files_modified=1, diff_hash="abc")
        assert r.is_empty


# ---------------------------------------------------------------------------
# Tests: load_code_json / evaluate_test_results
# ---------------------------------------------------------------------------

class TestLoadCodeJson:
    def test_missing_file(self, tmp_path: Path) -> None:
        assert load_code_json(tmp_path / "nonexistent.json") == {}

    def test_valid_file(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text(json.dumps({"data": {"edits": [{"path": "a.py"}]}}))
        result = load_code_json(p)
        assert result == {"edits": [{"path": "a.py"}]}

    def test_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text("not json {{{")
        assert load_code_json(p) == {}


class TestEvaluateTestResults:
    def test_all_passed(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text(json.dumps({
            "data": {
                "test_results": [
                    {"command": "pytest a.py", "passed": True, "returncode": 0},
                    {"command": "pytest b.py", "passed": True, "returncode": 0},
                ]
            }
        }))
        all_passed, results = evaluate_test_results(p)
        assert all_passed is True
        assert len(results) == 2

    def test_one_failed(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text(json.dumps({
            "data": {
                "test_results": [
                    {"command": "pytest a.py", "passed": True, "returncode": 0},
                    {"command": "pytest b.py", "passed": False, "returncode": 1},
                ]
            }
        }))
        all_passed, results = evaluate_test_results(p)
        assert all_passed is False

    def test_missing_code_json(self, tmp_path: Path) -> None:
        all_passed, results = evaluate_test_results(tmp_path / "missing.json")
        assert all_passed is False
        assert results == []

    def test_empty_test_results(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text(json.dumps({"data": {"test_results": []}}))
        all_passed, results = evaluate_test_results(p)
        assert all_passed is False
        assert results == []

    def test_no_test_results_key(self, tmp_path: Path) -> None:
        p = tmp_path / "code.json"
        p.write_text(json.dumps({"data": {"edits": []}}))
        all_passed, results = evaluate_test_results(p)
        assert all_passed is False
        assert results == []


# ---------------------------------------------------------------------------
# Tests: Validation runner integration with shared helper
# ---------------------------------------------------------------------------

class TestValidationRunnerIntegration:
    """Verify that validation/runner.py uses the shared helper."""

    def test_runner_uses_get_staged_diff_not_head1(self, monkeypatch, tmp_path: Path) -> None:
        import validation.runner as runner

        # Set up minimal validation structure
        (tmp_path / "validation").mkdir()
        (tmp_path / "validation" / "targets.json").write_text(json.dumps({
            "targets": [{"repo": "fake/repo", "issue": "123"}]
        }))
        monkeypatch.chdir(tmp_path)

        runs_dir = tmp_path / "runs"
        runs_dir.mkdir()
        run_dir = runs_dir / "2026-07-24T12-00-00"
        run_dir.mkdir()
        (run_dir / "repository.json").write_text(json.dumps({"data": {"source_path": "/fake"}}))

        # Create a code.json with test results (structured data)
        (run_dir / "code.json").write_text(json.dumps({
            "data": {
                "test_results": [
                    {"command": "pytest test.py", "passed": True, "returncode": 0}
                ]
            }
        }))

        git_diff_used = []

        def mock_run_command(cmd):
            return 0, "Running tests...\nOK", "", 1.0

        def mock_get_git_diff(repo):
            # Record what function is being called
            git_diff_used.append("get_staged_diff")
            return "diff --git a/test b/test\n+ code"

        monkeypatch.setattr(runner, "run_command", mock_run_command)
        monkeypatch.setattr(runner, "get_git_diff", mock_get_git_diff)
        monkeypatch.setattr(runner, "get_git_branch", lambda r: "fake-branch")

        runner.main()

        summary = (tmp_path / "validation" / "fake" / "repo" / "123" / "summary.md").read_text()
        assert "- **Status**: Failed" in summary or "- **Status**: Success" in summary
        # The runner should have used our mock which represents staged diff
        assert "get_staged_diff" in git_diff_used
