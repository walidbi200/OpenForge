"""Tests for the benchmarking framework."""

import json
from pathlib import Path

from typer.testing import CliRunner

from openforge.cli import app
from openforge.models import RepositoryContext
from openforge.benchmarks.runner import BenchmarkRunner

runner = CliRunner()


def test_benchmark_runner_scoring(tmp_path: Path, monkeypatch):
    """Test scoring logic of BenchmarkRunner."""
    repos_json = tmp_path / "repositories.json"
    repos_json.write_text(json.dumps({
        "repositories": [
            {
                "name": "TestRepo",
                "url": "https://github.com/test/repo",
                "expected": {
                    "language": "Python",
                    "framework": "none",
                    "package_manager": "uv",
                    "formatter": "ruff",
                    "linter": "ruff",
                    "test_command": "pytest",
                    "ci": "GitHub Actions"
                }
            }
        ]
    }))

    class MockAnalyzer:
        def __init__(self, *args, **kwargs):
            pass

        def resolve(self, url):
            return Path("/mock/path"), "test-repo"

        def analyze(self, local_path, repo_label):
            return RepositoryContext(
                repo=repo_label,
                source_path=str(local_path),
                language="Python",
                framework=None,
                package_manager="pip",  # Incorrect! expected uv
                formatter="ruff",
                linter="ruff",
                test_command="pytest",
                ci="GitHub Actions"
            )

    monkeypatch.setattr("openforge.benchmarks.runner.RepositoryAnalyzer", MockAnalyzer)

    bm_runner = BenchmarkRunner(tmp_path, repos_json)
    results = bm_runner.run()

    assert len(results.repos) == 1
    repo_result = results.repos[0]
    
    # 6 out of 7 correct (package_manager is wrong)
    assert repo_result.correct_count == 6
    assert repo_result.total_count == 7
    assert repo_result.accuracy == (6 / 7) * 100

    assert len(results.failed_repos) == 1
    assert "TestRepo" in results.failed_repos
    assert len(results.perfect_repos) == 0

    assert results.overall_accuracy == (6 / 7) * 100
    assert results.field_accuracy["language"] == 100.0
    assert results.field_accuracy["package_manager"] == 0.0


def test_cli_benchmark_command(tmp_path: Path, monkeypatch):
    """Test the openforge benchmark command."""
    repos_json = tmp_path / "repositories.json"
    repos_json.write_text(json.dumps({
        "repositories": [
            {
                "name": "TestRepo",
                "url": "https://github.com/test/repo",
                "expected": {
                    "language": "Python",
                    "framework": "none",
                    "package_manager": "uv",
                    "formatter": "ruff",
                    "linter": "ruff",
                    "test_command": "pytest",
                    "ci": "GitHub Actions"
                }
            }
        ]
    }))

    # We need to monkeypatch the path where cli.py looks for repositories.json
    monkeypatch.setattr("openforge.cli.Path.exists", lambda self: True)
    
    class MockAnalyzer:
        def __init__(self, *args, **kwargs):
            pass
        def resolve(self, url):
            return Path("/mock/path"), "test-repo"
        def analyze(self, local_path, repo_label):
            return RepositoryContext(
                repo=repo_label,
                source_path=str(local_path),
                language="Python"
            )
            
    monkeypatch.setattr("openforge.benchmarks.runner.RepositoryAnalyzer", MockAnalyzer)
    
    # Mock json load in runner since the path might be hardcoded in cli.py
    # Actually cli.py passes json_path to BenchmarkRunner. We can mock BenchmarkRunner.__init__ 
    # to override the json_path, or just let it use the mock json.
    original_init = BenchmarkRunner.__init__
    
    def mock_init(self, workspace_dir, json_path):
        original_init(self, workspace_dir, repos_json)
        
    monkeypatch.setattr("openforge.benchmarks.runner.BenchmarkRunner.__init__", mock_init)
    
    # Run command
    result = runner.invoke(app, ["benchmark", "--json"])
    assert result.exit_code == 0
    assert "overall_accuracy" in result.stdout
