import pytest
import subprocess

def test_invalid_repository():
    res = subprocess.run(["uv", "run", "openforge", "inspect", "https://github.com/invalid/invalid"], capture_output=True, text=True)
    assert res.returncode != 0
    assert "fatal: could not read Username for 'https://github.com'" in res.stdout

def test_missing_files_in_plan():
    # If a planner generates an invalid plan missing a field, pydantic should fail gracefully
    pass
