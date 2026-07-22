"""Unit tests for CodingAgent deterministic logic.

Tests use real git repos in tmp_path (git init + real files), following
the same pattern as test_repository_analyzer.py.

No test asserts anything about the correctness of generated code — only
about the deterministic, structural behaviour of the agent.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from openforge.agents.coder import (
    CodingAgent,
    CoderError,
    CodeResult,
    FileEdit,
    _branch_name,
    _slugify,
)
from openforge.utils import extract_json
from openforge.config import Settings
from openforge.models import ImplementationPlan, RepositoryContext


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_SETTINGS_WITH_KEY = Settings(
    openrouter_api_key="sk-or-test-key",
    openrouter_model="anthropic/claude-sonnet-4.6",
)
_SETTINGS_NO_KEY = Settings(openrouter_api_key=None)


def _make_agent() -> CodingAgent:
    return CodingAgent(_SETTINGS_WITH_KEY)


def _make_plan(files: list[str], goal: str = "Fix the bug.") -> ImplementationPlan:
    return ImplementationPlan(
        goal=goal,
        files_to_modify=files,
        implementation_steps=["Step 1: make the change."],
        tests_to_run=[],
    )


def _make_repo(path: Path) -> RepositoryContext:
    return RepositoryContext(
        repo="test/repo",
        source_path=str(path),
        language="python",
        test_command="pytest",
    )


def _git_init(path: Path) -> None:
    """Initialise a git repo suitable for testing."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@openforge.local"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "OpenForge Test"],
        cwd=path, check=True, capture_output=True,
    )
    # Create an initial commit so branch creation works.
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path, check=True, capture_output=True,
    )


def _llm_response(files: list[dict], summary: str = "Done.") -> str:
    """Build a fake LLM response JSON string."""
    return json.dumps({"files": files, "summary": summary})


def _mock_llm(content: str) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


class TestSlugify:
    def test_lowercases_and_replaces_special_chars(self):
        assert _slugify("Fix Content-Encoding bug") == "fix-content-encoding-bug"

    def test_truncates_to_max_len(self):
        result = _slugify("a" * 50, max_len=10)
        assert len(result) <= 10

    def test_strips_leading_trailing_dashes(self):
        result = _slugify("---hello---")
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestBranchName:
    def test_includes_issue_number(self):
        name = _branch_name(1988, "Fix Content-Encoding header")
        assert name.startswith("openforge/1988-")

    def test_no_issue_number(self):
        name = _branch_name(None, "Fix the bug")
        assert name.startswith("openforge/")
        assert "None" not in name


class TestExtractJson:
    def test_plain_json(self):
        raw = '{"files": [], "summary": "done"}'
        assert extract_json(raw) == raw

    def test_fenced_json(self):
        raw = '```json\n{"files": []}\n```'
        assert extract_json(raw) == '{"files": []}'


# ---------------------------------------------------------------------------
# _read_files tests
# ---------------------------------------------------------------------------


class TestReadFiles:
    def test_existing_file_is_read(self, tmp_path: Path):
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        agent = _make_agent()
        result = agent._read_files(tmp_path, ["src.py"])
        assert result["src.py"] == "x = 1\n"

    def test_nonexistent_file_returns_none(self, tmp_path: Path):
        agent = _make_agent()
        result = agent._read_files(tmp_path, ["does_not_exist.py"])
        assert result["does_not_exist.py"] is None

    def test_oversized_file_raises_coder_error(self, tmp_path: Path):
        big = tmp_path / "big.py"
        big.write_text("x = 1\n" * 20000, encoding="utf-8")  # ~120 KB
        assert big.stat().st_size > 100 * 1024
        agent = _make_agent()
        with pytest.raises(CoderError, match="exceeds"):
            agent._read_files(tmp_path, ["big.py"])

    def test_binary_file_raises_coder_error(self, tmp_path: Path):
        bad = tmp_path / "binary.py"
        bad.write_bytes(b"\xff\xfe" + b"\x00" * 100)
        agent = _make_agent()
        with pytest.raises(CoderError, match="UTF-8"):
            agent._read_files(tmp_path, ["binary.py"])


# ---------------------------------------------------------------------------
# _parse_response tests
# ---------------------------------------------------------------------------


class TestParseResponse:
    def test_valid_response_parsed(self):
        agent = _make_agent()
        raw = _llm_response([
            {"path": "a.py", "action": "modify", "new_content": "x = 2\n"}
        ])
        edits = agent._parse_response(raw, ["a.py"])
        assert len(edits) == 1
        assert edits[0].path == "a.py"
        assert edits[0].new_content == "x = 2\n"

    def test_malformed_json_raises_coder_error(self):
        agent = _make_agent()
        with pytest.raises(CoderError, match="non-JSON"):
            agent._parse_response("not json", ["a.py"])

    def test_entry_with_empty_path_is_skipped(self):
        agent = _make_agent()
        raw = _llm_response([
            {"path": "", "action": "modify", "new_content": "x = 1\n"},
            {"path": "a.py", "action": "modify", "new_content": "x = 2\n"},
        ])
        edits = agent._parse_response(raw, ["a.py"])
        assert len(edits) == 1
        assert edits[0].path == "a.py"


# ---------------------------------------------------------------------------
# Full implement() integration tests using real git repos
# ---------------------------------------------------------------------------


class TestImplement:
    def test_modifying_existing_file_is_detectable_via_git_diff(
        self, tmp_path: Path
    ):
        """LLM modifies src.py → content written → git diff is non-empty."""
        _git_init(tmp_path)
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "src.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add src"],
            cwd=tmp_path, check=True, capture_output=True,
        )

        repo = _make_repo(tmp_path)
        plan = _make_plan(["src.py"])
        llm_content = _llm_response([
            {"path": "src.py", "action": "modify", "new_content": "x = 2\n"}
        ])
        agent = _make_agent()
        with patch("openforge.agents.coder.httpx.post", return_value=_mock_llm(llm_content)):
            result = agent.implement(repo, plan, issue_number=1)

        assert "src.py" in result.git_diff
        assert result.branch_name.startswith("openforge/")
        assert result.unexpected_files == []

    def test_creating_nonexistent_file_is_not_an_error(self, tmp_path: Path):
        """LLM creates a new file → file appears on disk, diff shows addition."""
        _git_init(tmp_path)
        repo = _make_repo(tmp_path)
        plan = _make_plan(["new_module.py"])
        llm_content = _llm_response([
            {"path": "new_module.py", "action": "create", "new_content": "# new\n"}
        ])
        agent = _make_agent()
        with patch("openforge.agents.coder.httpx.post", return_value=_mock_llm(llm_content)):
            result = agent.implement(repo, plan, issue_number=2)

        assert (tmp_path / "new_module.py").exists()
        assert (tmp_path / "new_module.py").read_text() == "# new\n"
        assert "new_module.py" in result.git_diff

    def test_file_outside_plan_is_flagged_not_written(self, tmp_path: Path):
        """LLM returns a path not in plan.files_to_modify → flagged, not written."""
        _git_init(tmp_path)
        (tmp_path / "allowed.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "allowed.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add allowed"],
            cwd=tmp_path, check=True, capture_output=True,
        )

        repo = _make_repo(tmp_path)
        plan = _make_plan(["allowed.py"])  # only allowed.py is in the plan
        llm_content = _llm_response([
            {"path": "allowed.py", "action": "modify", "new_content": "x = 2\n"},
            {"path": "sneaky.py", "action": "create", "new_content": "evil = True\n"},
        ])
        agent = _make_agent()
        with patch("openforge.agents.coder.httpx.post", return_value=_mock_llm(llm_content)):
            result = agent.implement(repo, plan, issue_number=3)

        # sneaky.py must NOT have been written
        assert not (tmp_path / "sneaky.py").exists()
        # it must be in unexpected_files
        assert "sneaky.py" in result.unexpected_files
        # allowed.py was written
        assert (tmp_path / "allowed.py").read_text() == "x = 2\n"

    def test_oversized_file_in_plan_raises_before_llm_call(self, tmp_path: Path):
        """A file > 100 KB in plan raises CoderError before LLM is ever called."""
        _git_init(tmp_path)
        big = tmp_path / "big.py"
        big.write_text("x = 1\n" * 20000, encoding="utf-8")  # ~120 KB
        assert big.stat().st_size > 100 * 1024

        repo = _make_repo(tmp_path)
        plan = _make_plan(["big.py"])
        agent = _make_agent()
        with pytest.raises(CoderError, match="exceeds"):
            # No patch needed — CoderError fires before httpx.post is reached.
            agent.implement(repo, plan)

    def test_path_containment_and_traversal_are_rejected(self, tmp_path: Path):
        """A plan with malicious paths raises CoderError and does not write."""
        _git_init(tmp_path)
        repo = _make_repo(tmp_path)
        
        malicious_paths = [
            "../../etc/evil.txt",
            "/tmp/abs.txt",
            "a/../../escape.txt",
        ]
        
        for bad_path in malicious_paths:
            plan = _make_plan([bad_path])
            agent = _make_agent()
            with pytest.raises(CoderError, match="not allowed|outside the repo"):
                agent.implement(repo, plan, issue_number=42)


# ---------------------------------------------------------------------------
# Test runner logic
# ---------------------------------------------------------------------------


class TestRunTests:
    def test_run_tests_rejects_unsafe_commands(self, tmp_path: Path):
        agent = _make_agent()
        results = agent._run_tests(
            ["echo hi; touch /tmp/pwned", "pytest tests/"],
            cwd=tmp_path
        )
        assert len(results) == 2
        
        # The first command should be rejected because echo is not in allowlist
        assert results[0].returncode == 1
        assert "REJECTED" in results[0].stderr
        assert "allowlist" in results[0].stderr

        # The second command runs (fails gracefully with exit code from pytest, but wasn't rejected)
        # However, pytest might not exist in the mock environment, so it might return 1 with "not found"
        # The key is that it didn't say "REJECTED"
        assert "REJECTED" not in results[1].stderr

    def test_run_tests_rejects_shlex_error(self, tmp_path: Path):
        agent = _make_agent()
        results = agent._run_tests(
            ['pytest "unclosed string'],
            cwd=tmp_path
        )
        assert len(results) == 1
        assert results[0].returncode == 1
        assert "Malformed" in results[0].stderr


# ---------------------------------------------------------------------------
# Init guard
# ---------------------------------------------------------------------------


class TestCoderInit:
    def test_raises_without_api_key(self):
        with pytest.raises(CoderError, match="OPENROUTER_API_KEY"):
            CodingAgent(_SETTINGS_NO_KEY)
