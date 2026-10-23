"""Tests for validation/evaluate.py — deterministic failure classification.

Every test uses synthetic data — no LLM calls, no network, no Docker.
"""

import json
from pathlib import Path

from validation.evaluate import classify_failure, build_result, build_benchmark_report


# ── classify_failure ────────────────────────────────────────────────────


def _code_data(edits=None, summary="", test_results=None):
    return {
        "edits": edits or [],
        "summary": summary,
        "test_results": test_results or [],
    }


class TestClassifyFailure:
    def test_inspect_failure(self):
        assert classify_failure(1, 0, 0, _code_data(), "", "") == "Infrastructure failure"

    def test_planner_failure(self):
        assert classify_failure(0, 1, 0, _code_data(), "", "") == "Planner failure"

    def test_coder_parsing_failure_prose(self):
        d = _code_data(edits=[], summary="Failed: LLM returned prose instead of JSON after 3 attempts.")
        assert classify_failure(0, 0, 1, d, "", "") == "Coder parsing failure"

    def test_coder_parsing_failure_json(self):
        d = _code_data(edits=[], summary="Failed: invalid JSON response.")
        assert classify_failure(0, 0, 1, d, "", "") == "Coder parsing failure"

    def test_coder_failure_no_edits_generic(self):
        d = _code_data(edits=[], summary="Something went wrong.")
        assert classify_failure(0, 0, 1, d, "", "") == "Coder failure"

    def test_coder_failure_edits_but_no_tests(self):
        d = _code_data(edits=[{"path": "a.py"}], summary="ok", test_results=[])
        assert classify_failure(0, 0, 0, d, "", "") == "Coder failure"

    def test_sandbox_failure(self):
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": False, "stdout": "", "stderr": "=== DEPENDENCY INSTALLATION FAILED ==="}],
        )
        assert classify_failure(0, 0, 0, d, "", "=== DEPENDENCY INSTALLATION FAILED ===") == "Sandbox failure"

    def test_dependency_failure(self):
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": False, "stdout": "", "stderr": "ModuleNotFoundError: No module named 'flask'"}],
        )
        assert classify_failure(0, 0, 0, d, "", "ModuleNotFoundError: No module named 'flask'") == "Dependency failure"

    def test_compilation_failure(self):
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": False, "stdout": "", "stderr": "SyntaxError: invalid syntax"}],
        )
        assert classify_failure(0, 0, 0, d, "", "SyntaxError: invalid syntax") == "Compilation failure"

    def test_incorrect_implementation(self):
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": False, "stdout": "assert 1 == 2 failed", "stderr": "AssertionError"}],
        )
        assert classify_failure(0, 0, 0, d, "assert 1 == 2 failed", "AssertionError") == "Incorrect implementation"

    def test_success(self):
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": True, "stdout": "1 passed", "stderr": ""}],
        )
        assert classify_failure(0, 0, 0, d, "1 passed", "") == "Success"

    def test_dependency_failure_in_stdout(self):
        """ModuleNotFoundError can appear in stdout (pytest captures it)."""
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[{"passed": False, "stdout": "ImportError: cannot import name 'foo'", "stderr": ""}],
        )
        assert classify_failure(0, 0, 0, d, "ImportError: cannot import name 'foo'", "") == "Dependency failure"

    def test_partial_failure_still_incorrect(self):
        """Some tests pass, some fail → Incorrect implementation."""
        d = _code_data(
            edits=[{"path": "a.py"}],
            test_results=[
                {"passed": True, "stdout": "passed", "stderr": ""},
                {"passed": False, "stdout": "failed", "stderr": "AssertionError"},
            ],
        )
        assert classify_failure(0, 0, 0, d, "passed failed", "AssertionError") == "Incorrect implementation"


# ── build_result ────────────────────────────────────────────────────────


class TestBuildResult:
    def test_build_result_success(self, tmp_path):
        # Write synthetic artifacts
        (tmp_path / "plan.json").write_text(json.dumps({
            "data": {"goal": "Fix it", "edit_targets": [{"file_path": "src/a.py"}]}
        }))
        (tmp_path / "code.json").write_text(json.dumps({
            "data": {
                "edits": [{"path": "src/a.py"}],
                "summary": "Fixed it.",
                "test_results": [{"passed": True, "stdout": "1 passed", "stderr": ""}],
            }
        }))
        (tmp_path / "prompt_metrics.json").write_text(json.dumps([
            {"agent": "PlannerAgent", "total_tokens": 100, "retry_count": 0, "model": "test-model"},
            {"agent": "CodingAgent", "total_tokens": 200, "retry_count": 0, "model": "test-model"},
        ]))

        result = build_result(
            repo="test/repo", issue_number=42, model="test-model", run_id="test-run",
            inspect_exit=0, plan_exit=0, code_exit=0,
            timings={"inspect": 1.0, "analyze": 0.5, "plan": 2.0, "code": 3.0},
            run_dir=tmp_path, dest_dir=tmp_path, git_diff="diff --git a/src/a.py b/src/a.py",
            expected_files=["src/a.py"],
        )

        assert result["classification"] == "Success"
        assert result["outcome"] == "success"
        assert result["planner_success"] is True
        assert result["coder_success"] is True
        assert result["tests_passed"] is True
        assert result["tests_passed_count"] == 1
        assert result["files_edited"] == 1
        assert result["token_usage"]["total_tokens"] == 300
        assert result["token_usage"]["planner_retries"] == 0
        assert result["runtime"]["total"] == 6.5

    def test_build_result_coder_parsing_failure(self, tmp_path):
        (tmp_path / "plan.json").write_text(json.dumps({
            "data": {"goal": "Fix it", "edit_targets": [{"file_path": "src/a.py"}]}
        }))
        (tmp_path / "code.json").write_text(json.dumps({
            "data": {
                "edits": [],
                "summary": "Failed: LLM returned prose instead of JSON after 3 attempts.",
                "test_results": [],
            }
        }))

        result = build_result(
            repo="test/repo", issue_number=42, model="test-model", run_id="test-run",
            inspect_exit=0, plan_exit=0, code_exit=1,
            timings={"inspect": 1.0, "analyze": 0.5, "plan": 2.0, "code": 3.0},
            run_dir=tmp_path, dest_dir=tmp_path, git_diff="",
        )

        assert result["classification"] == "Coder parsing failure"
        assert result["outcome"] == "failure"
        assert result["coder_success"] is False
        assert result["tests_executed"] is False
        assert result["files_edited"] == 0

    def test_build_result_incorrect_implementation(self, tmp_path):
        (tmp_path / "plan.json").write_text(json.dumps({
            "data": {"goal": "Fix it", "edit_targets": [{"file_path": "src/a.py"}]}
        }))
        (tmp_path / "code.json").write_text(json.dumps({
            "data": {
                "edits": [{"path": "src/a.py"}],
                "summary": "Fixed it.",
                "test_results": [
                    {"passed": True, "stdout": "passed", "stderr": ""},
                    {"passed": False, "stdout": "failed", "stderr": "AssertionError: 1 != 2"},
                ],
            }
        }))

        result = build_result(
            repo="test/repo", issue_number=42, model="test-model", run_id="test-run",
            inspect_exit=0, plan_exit=0, code_exit=0,
            timings={"inspect": 1.0, "analyze": 0.5, "plan": 2.0, "code": 3.0},
            run_dir=tmp_path, dest_dir=tmp_path, git_diff="diff --git a/src/a.py b/src/a.py",
        )

        assert result["classification"] == "Incorrect implementation"
        assert result["outcome"] == "failure"
        assert result["tests_passed"] is False
        assert result["tests_passed_count"] == 1
        assert result["tests_total_count"] == 2


# ── build_benchmark_report ──────────────────────────────────────────────


class TestBuildBenchmarkReport:
    def test_empty_results(self):
        report = build_benchmark_report([])
        assert report["total_issues"] == 0
        assert report["successes"] == 0
        assert report["success_rate"] == 0.0

    def test_mixed_results(self):
        results = [
            {"outcome": "success", "classification": "Success",
             "token_usage": {"total_tokens": 100}, "runtime": {"total": 10.0}},
            {"outcome": "failure", "classification": "Coder parsing failure",
             "token_usage": {"total_tokens": 200}, "runtime": {"total": 20.0}},
            {"outcome": "failure", "classification": "Incorrect implementation",
             "token_usage": {"total_tokens": 300}, "runtime": {"total": 30.0}},
        ]
        report = build_benchmark_report(results)

        assert report["total_issues"] == 3
        assert report["successes"] == 1
        assert report["failures"] == 2
        assert report["success_rate"] == 33.3
        assert report["total_tokens"] == 600
        assert report["total_runtime_seconds"] == 60.0
        assert report["classification_counts"]["Success"] == 1
        assert report["classification_counts"]["Coder parsing failure"] == 1
        assert report["classification_counts"]["Incorrect implementation"] == 1
