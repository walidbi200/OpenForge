import json
from pathlib import Path
import pytest
import shutil
import subprocess

# We will test the validation runner logic via subprocess and mock outputs.
# The actual evaluation logic is inside validation/runner.py, which operates by running commands
# and generating summary.md. We can write a test that creates a fake `validation/targets.json`,
# runs `validation/runner.py`, but we mock `subprocess.run` inside it.

def _write_code_json(run_dir: Path, *, test_results: list[dict] | None = None) -> None:
    """Write a code.json with structured test results into the run dir."""
    data = {"test_results": test_results if test_results is not None else []}
    (run_dir / "code.json").write_text(json.dumps({"data": data}))


def test_validation_runner_strict_success(monkeypatch, tmp_path):
    import validation.runner as runner

    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({
        "targets": [{"repo": "fake/repo", "issue": "123"}]
    }))

    monkeypatch.chdir(tmp_path)
    (tmp_path / "validation").mkdir()
    shutil.move(targets, tmp_path / "validation" / "targets.json")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "2026-07-24T12-00-00"
    run_dir.mkdir()

    def mock_run_command(cmd):
        return 0, "Running tests... FAKED_OUTPUT\nSuccess", "", 1.0

    monkeypatch.setattr(runner, "run_command", mock_run_command)

    def mock_get_git_diff(repo):
        return "diff --git a/test b/test\n+ code"

    def mock_get_git_branch(repo):
        return "fake-branch"

    monkeypatch.setattr(runner, "get_git_diff", mock_get_git_diff)
    monkeypatch.setattr(runner, "get_git_branch", mock_get_git_branch)

    (run_dir / "repository.json").write_text(json.dumps({"data": {"source_path": "/fake"}}))
    _write_code_json(run_dir, test_results=[
        {"command": "pytest test.py", "passed": True, "returncode": 0}
    ])

    runner.main()

    summary = (tmp_path / "validation" / "fake" / "repo" / "123" / "summary.md").read_text()
    assert "- **Status**: Success" in summary
    assert "- **Files Modified**: 1" in summary
    assert "- **Test Status**: Passed" in summary


def test_validation_runner_fails_on_empty_diff(monkeypatch, tmp_path):
    import validation.runner as runner

    targets = tmp_path / "targets.json"
    targets.write_text(json.dumps({
        "targets": [{"repo": "fake/repo", "issue": "123"}]
    }))

    monkeypatch.chdir(tmp_path)
    (tmp_path / "validation").mkdir()
    shutil.move(targets, tmp_path / "validation" / "targets.json")

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "2026-07-24T12-00-00"
    run_dir.mkdir()

    def mock_run_command(cmd):
        return 0, "Running tests... FAKED_OUTPUT\nSuccess", "", 1.0

    monkeypatch.setattr(runner, "run_command", mock_run_command)

    def mock_get_git_diff(repo):
        return ""

    monkeypatch.setattr(runner, "get_git_diff", mock_get_git_diff)
    monkeypatch.setattr(runner, "get_git_branch", lambda r: "fake-branch")

    (run_dir / "repository.json").write_text(json.dumps({"data": {"source_path": "/fake"}}))
    _write_code_json(run_dir, test_results=[
        {"command": "pytest test.py", "passed": True, "returncode": 0}
    ])

    runner.main()

    summary = (tmp_path / "validation" / "fake" / "repo" / "123" / "summary.md").read_text()
    assert "- **Status**: Failed" in summary
    assert "- **Files Modified**: 0" in summary


def test_validation_runner_fails_on_test_failure(monkeypatch, tmp_path):
    import validation.runner as runner

    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "targets.json").write_text(json.dumps({
        "targets": [{"repo": "fake/repo", "issue": "123"}]
    }))
    monkeypatch.chdir(tmp_path)

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "2026-07-24T12-00-00"
    run_dir.mkdir()

    def mock_run_command(cmd):
        return 0, "Running tests...\nOK", "", 1.0

    monkeypatch.setattr(runner, "run_command", mock_run_command)

    def mock_get_git_diff(repo):
        return "diff --git a/test b/test\n+ code"

    monkeypatch.setattr(runner, "get_git_diff", mock_get_git_diff)
    monkeypatch.setattr(runner, "get_git_branch", lambda r: "fake-branch")

    (run_dir / "repository.json").write_text(json.dumps({"data": {"source_path": "/fake"}}))
    _write_code_json(run_dir, test_results=[
        {"command": "pytest test.py", "passed": False, "returncode": 1}
    ])

    runner.main()

    summary = (tmp_path / "validation" / "fake" / "repo" / "123" / "summary.md").read_text()
    assert "- **Status**: Failed" in summary
    assert "- **Test Status**: Failed" in summary


def test_validation_runner_fails_on_missing_code_json(monkeypatch, tmp_path):
    """Without code.json, test status must be 'Failed' (never assume success)."""
    import validation.runner as runner

    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "targets.json").write_text(json.dumps({
        "targets": [{"repo": "fake/repo", "issue": "123"}]
    }))
    monkeypatch.chdir(tmp_path)

    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "2026-07-24T12-00-00"
    run_dir.mkdir()

    def mock_run_command(cmd):
        return 0, "Running tests... OK", "", 1.0

    monkeypatch.setattr(runner, "run_command", mock_run_command)
    monkeypatch.setattr(runner, "get_git_diff", lambda r: "diff --git a/test b/test\n+ code")
    monkeypatch.setattr(runner, "get_git_branch", lambda r: "fake-branch")

    (run_dir / "repository.json").write_text(json.dumps({"data": {"source_path": "/fake"}}))
    # No code.json written

    runner.main()

    summary = (tmp_path / "validation" / "fake" / "repo" / "123" / "summary.md").read_text()
    assert "- **Status**: Failed" in summary
    assert "- **Test Status**: Failed" in summary
    assert "- **Tests Executed**: No" in summary
