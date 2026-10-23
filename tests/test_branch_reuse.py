import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from openforge.agents.coder import CodingAgent
from openforge.agents.coder import CoderError
from openforge.config import Settings

def test_branch_reuse(tmp_path: Path):
    """Test that CodingAgent safely reuses a branch if it already exists."""
    # Setup dummy git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    
    (tmp_path / "file.txt").write_text("hello")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # Create the branch manually first
    subprocess.run(["git", "branch", "test-branch"], cwd=tmp_path, check=True)

    agent = CodingAgent(Settings(agentrouter_api_key="test"))
    agent._load_prompt = MagicMock(return_value="test")
    
    # Run the branch creation logic
    agent._git_create_branch(tmp_path, "test-branch")
    
    # Verify we are on the branch
    res = subprocess.run(["git", "branch", "--show-current"], cwd=tmp_path, capture_output=True, text=True)
    assert res.stdout.strip() == "test-branch"


def test_dirty_worktree_aborts_without_cleanup(tmp_path: Path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    tracked = tmp_path / "file.txt"
    tracked.write_text("original\n")
    subprocess.run(["git", "add", "file.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)
    tracked.write_text("user change\n")
    (tmp_path / "untracked.txt").write_text("keep me\n")

    agent = CodingAgent(Settings(agentrouter_api_key="test"))

    with pytest.raises(CoderError) as exc_info:
        agent._git_create_branch(tmp_path, "new-branch")

    message = str(exc_info.value)
    assert "file.txt" in message
    assert "untracked.txt" in message
    assert tracked.read_text() == "user change\n"
    assert (tmp_path / "untracked.txt").read_text() == "keep me\n"
