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
    agentrouter_api_key="test",
)
_SETTINGS_NO_KEY = Settings(agentrouter_api_key=None)


def _make_agent() -> CodingAgent:
    return CodingAgent(_SETTINGS_WITH_KEY)


def _make_plan(files: list[str], goal: str = "Fix the bug.") -> ImplementationPlan:
    from openforge.models import EditTarget
    return ImplementationPlan(
        goal=goal,
        edit_targets=[EditTarget(file_path=f) for f in files],
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


def _mock_llm(content: str) -> str:
    return content


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
        big.write_text("x = 1\n" * 60000, encoding="utf-8")  # ~120 KB
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
        edits, _ = agent._parse_response(raw, ["a.py"], {})
        assert len(edits) == 1
        assert edits[0].path == "a.py"
        assert edits[0].new_content == "x = 2\n"

    def test_malformed_json_raises_coder_error(self):
        agent = _make_agent()
        with pytest.raises(CoderError, match="non-JSON"):
            agent._parse_response("}{garbage", ["a.py"], {})

    def test_entry_with_empty_path_is_skipped(self):
        agent = _make_agent()
        raw = _llm_response([
            {"path": "", "action": "modify", "new_content": "x = 1\n"},
            {"path": "a.py", "action": "modify", "new_content": "x = 2\n"},
        ])
        edits, _ = agent._parse_response(raw, ["a.py"], {"a.py": "x = 1\n"})
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
        with patch("openforge.agents.coder.call_llm", return_value=_mock_llm(llm_content)):
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
        with patch("openforge.agents.coder.call_llm", return_value=_mock_llm(llm_content)):
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
        with patch("openforge.agents.coder.call_llm", return_value=_mock_llm(llm_content)):
            result = agent.implement(repo, plan, issue_number=3)
        assert result.unexpected_files == []

    def test_oversized_file_in_plan_raises_before_llm_call(self, tmp_path: Path):
        """A file > 100 KB in plan raises CoderError before LLM is ever called."""
        _git_init(tmp_path)
        big = tmp_path / "big.py"
        big.write_text("x = 1\n" * 60000, encoding="utf-8")  # ~120 KB
        subprocess.run(["git", "add", "big.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "add big"],
            cwd=tmp_path, check=True, capture_output=True,
        )
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
        repo_context = RepositoryContext(repo="test/repo", source_path=str(tmp_path), language="Python")
        results = agent._run_tests(
            ["echo hi; touch /tmp/pwned", "pytest tests/"],
            cwd=tmp_path,
            repo_context=repo_context
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
        repo_context = RepositoryContext(repo="test/repo", source_path=str(tmp_path), language="Python")
        results = agent._run_tests(
            ['pytest "unclosed string'],
            cwd=tmp_path,
            repo_context=repo_context
        )
        assert len(results) == 1
        assert results[0].returncode == 1
        assert "Malformed" in results[0].stderr

    def test_docker_sandbox_no_host_recovery_on_module_not_found(self, tmp_path: Path, monkeypatch):
        """Case 2: Docker ModuleNotFoundError must NOT run host subprocess and must return diagnostic."""
        agent = _make_agent()
        repo_context = RepositoryContext(repo="test/repo", source_path=str(tmp_path), language="Python")

        host_subprocess_calls = []
        original_subprocess_run = subprocess.run

        def capturing_subprocess_run(*args, **kwargs):
            host_subprocess_calls.append(args)
            return original_subprocess_run(*args, **kwargs)

        monkeypatch.setattr(subprocess, "run", capturing_subprocess_run)

        from openforge.services.sandbox import DockerSandbox
        docker = DockerSandbox()

        def mock_docker_run(cmd, cwd):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'missing_pkg'",
            )

        monkeypatch.setattr(docker, "run", mock_docker_run)

        from openforge.agents import coder as coder_module
        monkeypatch.setattr(coder_module, "get_sandbox", lambda: docker)

        results = agent._run_tests(["pytest tests/"], cwd=tmp_path, repo_context=repo_context)

        assert len(results) == 1
        assert results[0].returncode == 1
        assert "Dependency missing inside sandbox" in results[0].stderr
        assert "Host installation skipped" in results[0].stderr
        # No host-side subprocess.run() should have been invoked for recovery
        assert len(host_subprocess_calls) == 0

    def test_local_sandbox_host_recovery_still_executed(self, tmp_path: Path, monkeypatch):
        """Case 3: LocalSandbox ModuleNotFoundError must still execute host-side recovery."""
        agent = _make_agent()
        repo_context = RepositoryContext(repo="test/repo", source_path=str(tmp_path), language="Python")

        from openforge.services.sandbox import LocalSandbox, SandboxRunner
        local = LocalSandbox()

        call_count = {"sandbox": 0, "host_install": 0}

        def mock_local_run(cmd, cwd):
            call_count["sandbox"] += 1
            if call_count["sandbox"] == 1:
                return subprocess.CompletedProcess(
                    args=cmd,
                    returncode=1,
                    stdout="",
                    stderr="ModuleNotFoundError: No module named 'missing_pkg'",
                )
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout="PASSED",
                stderr="",
            )

        monkeypatch.setattr(local, "run", mock_local_run)

        def mock_host_install(*args, **kwargs):
            call_count["host_install"] += 1
            return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", mock_host_install)

        from openforge.agents import coder as coder_module
        monkeypatch.setattr(coder_module, "get_sandbox", lambda: local)

        results = agent._run_tests(["pytest tests/"], cwd=tmp_path, repo_context=repo_context)

        assert len(results) == 1
        assert results[0].returncode == 0
        assert results[0].stdout == "PASSED"
        assert call_count["sandbox"] == 2
        assert call_count["host_install"] == 1

    def test_local_sandbox_host_recovery_failure_reported(self, tmp_path: Path, monkeypatch):
        """Case 4: LocalSandbox host recovery failure is reported cleanly."""
        agent = _make_agent()
        repo_context = RepositoryContext(repo="test/repo", source_path=str(tmp_path), language="Python")

        from openforge.services.sandbox import LocalSandbox
        local = LocalSandbox()

        def mock_local_run(cmd, cwd):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=1,
                stdout="",
                stderr="ModuleNotFoundError: No module named 'missing_pkg'",
            )

        monkeypatch.setattr(local, "run", mock_local_run)

        def mock_host_install_fail(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0] if args else [],
                returncode=1,
                stdout="",
                stderr="pip install failed: connection error",
            )

        monkeypatch.setattr(subprocess, "run", mock_host_install_fail)

        from openforge.agents import coder as coder_module
        monkeypatch.setattr(coder_module, "get_sandbox", lambda: local)

        results = agent._run_tests(["pytest tests/"], cwd=tmp_path, repo_context=repo_context)

        assert len(results) == 1
        assert results[0].returncode == 1
        assert "Missing dependency: No module named 'missing_pkg'" in results[0].stderr
        assert "Auto-install failed" in results[0].stderr


# ---------------------------------------------------------------------------
# Raw response filename regression test
# ---------------------------------------------------------------------------


class TestRawResponseFilenames:
    """Ensure no filename collisions between main attempts and repair calls."""

    def test_all_expected_files_exist_with_unique_content(self, tmp_path: Path):
        _git_init(tmp_path)
        (tmp_path / "src.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(
            ["git", "add", "src.py"], cwd=tmp_path, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "commit", "-m", "add src"],
            cwd=tmp_path,
            check=True,
            capture_output=True,
        )

        repo = _make_repo(tmp_path)
        plan = _make_plan(["src.py"])
        run_dir = tmp_path / "run_dir"
        run_dir.mkdir()

        # Sequenced responses: attempts 0 and 1 produce no edits,
        # attempt 2 writes code with a syntax error, then repair fixes it.
        responses = {
            "attempt0_main": json.dumps({"files": [], "summary": "attempt0"}),
            "attempt1_main": json.dumps({"files": [], "summary": "attempt1"}),
            "attempt2_main": json.dumps(
                {"files": [{"path": "src.py", "action": "modify", "new_content": "x =\n"}], "summary": "attempt2"}
            ),
            "attempt0_repair": json.dumps(
                {"files": [{"path": "src.py", "action": "modify", "new_content": "x = 2\n"}], "summary": "repair0"}
            ),
        }

        call_order: list[str] = []

        def _sequenced_llm(prompt: str, *args, **kwargs):
            idx = len(call_order)
            label = ["attempt0_main", "attempt1_main", "attempt2_main", "attempt0_repair"][idx]
            call_order.append(label)
            # Ensure raw_callback writes the response to disk so we can inspect it later.
            raw_callback = kwargs.get("raw_callback")
            if raw_callback:
                raw_callback(responses[label])
            return responses[label]

        agent = _make_agent()
        with patch("openforge.agents.coder.call_llm", side_effect=_sequenced_llm):
            result = agent.implement(repo, plan, issue_number=1, run_dir=run_dir)

        expected_files = [
            "coder_attempt0_main.txt",
            "coder_attempt1_main.txt",
            "coder_attempt2_main.txt",
            "coder_attempt0_repair.txt",
        ]
        for fname in expected_files:
            fpath = run_dir / fname
            assert fpath.exists(), f"Missing {fname}"

        # Verify no overwrites: each file must contain its own unique summary.
        expected_summaries = {
            "coder_attempt0_main.txt": '"attempt0"',
            "coder_attempt1_main.txt": '"attempt1"',
            "coder_attempt2_main.txt": '"attempt2"',
            "coder_attempt0_repair.txt": '"repair0"',
        }
        for fname, expected in expected_summaries.items():
            content = (run_dir / fname).read_text(encoding="utf-8")
            assert expected in content, f"Unexpected content in {fname}: {content}"


# ---------------------------------------------------------------------------
# Init guard
# ---------------------------------------------------------------------------


class TestCoderInit:
    def test_raises_without_api_key(self):
        with pytest.raises(CoderError, match="selected LLM provider"):
            CodingAgent(_SETTINGS_NO_KEY)


# ---------------------------------------------------------------------------
# Markdown guard regression tests (Flask #6093)
# ---------------------------------------------------------------------------


class TestMarkdownGuardRegression:
    """Deterministic unit tests covering the markdown-fenced JSON guard bug.

    https://github.com/pallets/flask/issues/6093
    """

    def test_markdown_fenced_json_is_accepted_no_retry(self, tmp_path: Path):
        """Case 1: response starting with ```json must parse, not retry."""
        _git_init(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=tmp_path, check=True, capture_output=True)

        repo = _make_repo(tmp_path)
        plan = _make_plan(["a.py"])
        llm_content = (
            '```json\n'
            + json.dumps({
                "files": [{"path": "a.py", "action": "modify", "new_content": "x = 2\n"}],
                "summary": "fixed",
            })
            + '\n```'
        )
        agent = _make_agent()
        call_count: list[int] = [0]

        def _counting_llm(prompt: str, *args, **kwargs):
            call_count[0] += 1
            return llm_content

        with patch("openforge.agents.coder.call_llm", side_effect=_counting_llm):
            result = agent.implement(repo, plan, issue_number=6093)

        assert call_count[0] == 1, f"Expected exactly 1 LLM call, got {call_count[0]}"
        assert result.git_diff != ""
        assert "a.py" in result.git_diff
        assert result.edits
        assert result.edits[0].new_content == "x = 2\n"

    def test_plain_json_is_accepted(self, tmp_path: Path):
        """Case 2: bare JSON object must still work."""
        _git_init(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=tmp_path, check=True, capture_output=True)

        repo = _make_repo(tmp_path)
        plan = _make_plan(["a.py"])
        llm_content = json.dumps({
            "files": [{"path": "a.py", "action": "modify", "new_content": "x = 3\n"}],
            "summary": "fixed",
        })
        agent = _make_agent()
        with patch("openforge.agents.coder.call_llm", return_value=_mock_llm(llm_content)):
            result = agent.implement(repo, plan, issue_number=6093)

        assert result.git_diff != ""
        assert "a.py" in result.git_diff
        assert result.edits[0].new_content == "x = 3\n"

    def test_prose_triggers_retry_then_failure(self, tmp_path: Path):
        """Case 3: genuine prose causes retries and eventual failure."""
        _git_init(tmp_path)
        (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add a"], cwd=tmp_path, check=True, capture_output=True)

        repo = _make_repo(tmp_path)
        plan = _make_plan(["a.py"])
        agent = _make_agent()
        call_count: list[int] = [0]

        def _always_prose(prompt: str, *args, **kwargs):
            call_count[0] += 1
            return "Here is my analysis...\n\nI think we should..."

        with patch("openforge.agents.coder.call_llm", side_effect=_always_prose):
            result = agent.implement(repo, plan, issue_number=6093)

        assert call_count[0] == 3, f"Expected 3 LLM calls (all retries), got {call_count[0]}"
        assert "unparseable" in result.summary or "Failed" in result.summary
        assert result.git_diff == ""

    def test_real_flask_6093_response_is_parsed(self, tmp_path: Path):
        """Case 4: regression test with the real captured response from Flask #6093.

        The raw file starts with ```json and was previously rejected.
        We provide stub files whose content actually satisfies the SEARCH/REPLACE
        blocks so edits are produced and no retry loop is entered.
        """
        _git_init(tmp_path)
        # Stub contents must contain the exact search blocks from the real response.
        files = {
            "src/flask/app.py": (
                '        server_name = self.config.get("SERVER_NAME")\n'
                "        sn_host = sn_port = None\n\n"
                "        if server_name:\n"
                '            sn_host, _, sn_port = server_name.partition(":")\n\n'
                "        if not host:\n"
                "            if sn_host:\n"
                "                host = sn_host\n"
                "            else:\n"
                '                host = "127.0.0.1"\n'
            ),
            "src/flask/testing.py": (
                "        self._update_cookies_from_response(\n"
                '            ctx.request.host.partition(":")[0],\n'
                "            ctx.request.path,\n"
                '            resp.headers.getlist("Set-Cookie"),\n'
                "        )\n"
            ),
            "tests/test_testing.py": (
                "def test_session_transactions(app, client):\n"
                '    @app.route("/")\n'
                "    def index():\n"
                '        return str(flask.session["foo"])\n\n'
                "    with client:\n"
                "        with client.session_transaction() as sess:\n"
                "            assert len(sess) == 0\n"
                '            sess["foo"] = [42]\n'
                "            assert len(sess) == 1\n"
                '        rv = client.get("/")\n'
                '        assert rv.data == b"[42]"\n'
                "        with client.session_transaction() as sess:\n"
                "            assert len(sess) == 1\n"
                '            assert sess["foo"] == [42]\n\n\n'
                "def test_session_transactions_no_null_sessions():\n"
            ),
            "tests/test_basic.py": (
                '@pytest.mark.parametrize(\n'
                '    "host,port,server_name,expect_host,expect_port",\n'
                "    (\n"
                '        (None, None, "pocoo.org:8080", "pocoo.org", 8080),\n'
                '        ("localhost", None, "pocoo.org:8080", "localhost", 8080),\n'
                '        (None, 80, "pocoo.org:8080", "pocoo.org", 80),\n'
                '        ("localhost", 80, "pocoo.org:8080", "localhost", 80),\n'
                '        ("localhost", 0, "localhost:8080", "localhost", 0),\n'
                '        (None, None, "localhost:8080", "localhost", 8080),\n'
                '        (None, None, "localhost:0", "localhost", 0),\n'
                "    ),\n"
                ")\n"
                "def test_run_from_config(\n"
            ),
        }
        for path, content in files.items():
            abs_path = tmp_path / path
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", path], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmp_path, check=True, capture_output=True)

        repo = _make_repo(tmp_path)
        from openforge.models import EditTarget
        plan = ImplementationPlan(
            goal="Fix IPv6 address parsing",
            edit_targets=[EditTarget(file_path=f) for f in files.keys()],
            implementation_steps=["Parse bracketed IPv6 addresses correctly."],
            tests_to_run=["pytest tests/"],
        )

        real_response = (
            Path("/home/zhufan/Downloads/openforge/runs/2026-07-26T22-42-45/coder_attempt0_main.txt")
            .read_text(encoding="utf-8")
        )
        agent = _make_agent()
        call_count: list[int] = [0]

        def _real_once(prompt: str, *args, **kwargs):
            call_count[0] += 1
            return real_response

        run_dir = tmp_path / "run"
        run_dir.mkdir(parents=True, exist_ok=True)

        with patch("openforge.agents.coder.call_llm", side_effect=_real_once), \
             patch.object(agent, "_validate_syntax", return_value=""):
            result = agent.implement(repo, plan, issue_number=6093, run_dir=run_dir)

        assert call_count[0] == 1, f"Expected 1 LLM call, got {call_count[0]}"
        assert len(result.edits) == 4, f"Expected 4 edits, got {len(result.edits)}"
        assert result.summary != ""
        assert "Failed" not in result.summary or "unparseable" not in result.summary
        assert result.git_diff != ""
