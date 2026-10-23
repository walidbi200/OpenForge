import subprocess
import pytest
from pathlib import Path
from unittest.mock import patch
from openforge.services.sandbox import DockerSandbox, LocalSandbox, get_sandbox

def test_docker_sandbox_command_construction(monkeypatch, tmp_path):
    sandbox = DockerSandbox()
    
    captured_cmd = None
    captured_timeout = None
    
    def mock_run(cmd, capture_output, text, timeout):
        nonlocal captured_cmd, captured_timeout
        captured_cmd = cmd
        captured_timeout = timeout
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="success", stderr="")
        
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    res = sandbox.run(["pytest", "mytest.py; touch /tmp/should-not-run"], tmp_path)
    
    assert res.returncode == 0
    assert captured_timeout == 300.0
    
    # Verify security / isolation flags that are actually present
    assert "--cpus" in captured_cmd
    assert "--memory" in captured_cmd
    assert "bash" in captured_cmd

    # Verify install failure tracking is present in the bash command string
    cmd_str = " ".join(captured_cmd)
    assert "INSTALL_ERRORS" in cmd_str
    assert "DEPENDENCY INSTALLATION FAILED" in cmd_str
    # requirements.txt install should be conditional on file existence
    assert "[ -f requirements.txt ]" in cmd_str
    assert "[ -f requirements-dev.txt ]" in cmd_str
    assert captured_cmd[-2:] == ["pytest", "mytest.py; touch /tmp/should-not-run"]
    assert 'exec "$@"' in captured_cmd[captured_cmd.index("-c") + 1]

def test_docker_sandbox_timeout_handling(monkeypatch, tmp_path):
    sandbox = DockerSandbox()
    
    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=300.0, output=b"partial out")
        
    monkeypatch.setattr(subprocess, "run", mock_run_timeout)
    
    res = sandbox.run(["pytest"], tmp_path)
    
    assert res.returncode == 124
    assert res.stdout == "partial out"
    assert "ERROR: Command timed out" in res.stderr

def test_docker_sandbox_install_failure_diagnostic_in_stderr(monkeypatch, tmp_path):
    """Install failures are reported in stderr but do not override the test returncode.

    If tests pass (returncode 0) but some install commands failed (e.g. missing
    requirements.txt), the returncode must stay 0. The install failure header
    in stderr is diagnostic information for the caller.
    """
    sandbox = DockerSandbox()

    def mock_run_fail(cmd, capture_output, text, timeout):
        stderr = (
            "=== DEPENDENCY INSTALLATION FAILED ===\n"
            "FAILED: pip install -e .\n"
            "ERROR: Could not find a version that satisfies the requirement..."
        )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="all tests passed", stderr=stderr)

    monkeypatch.setattr(subprocess, "run", mock_run_fail)

    res = sandbox.run(["pytest"], tmp_path)

    assert res.returncode == 0
    assert "DEPENDENCY INSTALLATION FAILED" in res.stderr
    assert "FAILED: pip install -e ." in res.stderr

def test_local_sandbox_timeout_handling(monkeypatch, tmp_path):
    sandbox = LocalSandbox()
    
    def mock_run_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=300.0, output=b"partial local")
        
    monkeypatch.setattr(subprocess, "run", mock_run_timeout)
    
    res = sandbox.run(["pytest"], tmp_path)
    
    assert res.returncode == 124
    assert res.stdout == "partial local"
    assert "ERROR: Command timed out" in res.stderr


def test_get_sandbox_fails_when_docker_is_unavailable(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="Refusing to execute repository code on the host"):
        get_sandbox()
