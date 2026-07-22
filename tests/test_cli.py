"""Unit tests for the CLI commands."""

import json
from pathlib import Path
from typer.testing import CliRunner

from openforge.cli import app
from openforge.config import Settings

runner = CliRunner()


def test_code_blocks_requires_human_true(tmp_path: Path, monkeypatch):
    """Test that `code` refuses to run a requires_human=True plan without --force."""
    # Setup mock runs directory
    runs_dir = tmp_path / "runs"
    run_dir = runs_dir / "test-run"
    run_dir.mkdir(parents=True)
    
    # Mock settings
    settings = Settings(
        openrouter_api_key="sk-or-test-key",
        runs_dir=runs_dir,
    )
    monkeypatch.setattr("openforge.cli.get_settings", lambda: settings)
    
    # Write repository.json
    (run_dir / "repository.json").write_text(json.dumps({
        "data": {
            "repo": "test/repo",
            "source_path": str(tmp_path),
            "language": "python",
            "test_command": "pytest"
        }
    }))
    
    # Write plan.json with requires_human=True
    (run_dir / "plan.json").write_text(json.dumps({
        "data": {
            "goal": "Fix things",
            "files_to_modify": [],
            "implementation_steps": [],
            "tests_to_run": [],
            "risks": ["Danger"],
            "requires_human": True
        }
    }))
    
    # Run without --force should fail
    result = runner.invoke(app, ["code", "--run", "test-run"])
    assert result.exit_code == 1
    assert "The plan requires human sign-off before coding" in result.stdout
    
    # Run with --force should succeed (or at least get past the gate, might fail later due to missing issue_facts or llm mock, but let's mock agent.implement)
    with monkeypatch.context() as m:
        m.setattr("openforge.cli.CodingAgent.implement", lambda *args, **kwargs: None)
        # Mock CodeResult
        from openforge.agents.coder import CodeResult
        dummy_result = CodeResult(branch_name="test-branch")
        m.setattr("openforge.cli.CodingAgent.implement", lambda *args, **kwargs: dummy_result)
        
        result_force = runner.invoke(app, ["code", "--run", "test-run", "--force"])
        assert result_force.exit_code == 0
        assert "Proceeding with requires_human=True" in result_force.stdout or "Loaded plan" in result_force.stdout
