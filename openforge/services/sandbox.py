"""Sandbox abstraction for safely running test commands.

Runs tests inside an ephemeral Docker container if available,
falling back to local subprocess execution.
"""

import subprocess
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

class SandboxRunner:
    """Base interface for running shell commands safely."""
    def run(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        raise NotImplementedError

class LocalSandbox(SandboxRunner):
    """Executes commands directly on the host (insecure fallback)."""
    def run(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        logger.warning("Running test natively on host (LocalSandbox) - INSECURE.")
        try:
            return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=300.0)
        except subprocess.TimeoutExpired as e:
            logger.error("LocalSandbox test timed out after 300s.")
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=124,
                stdout=e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else (e.stdout or ""),
                stderr="ERROR: Command timed out after 300s."
            )

class DockerSandbox(SandboxRunner):
    """Executes commands inside a Python 3.11 Docker container."""

    def run(self, cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
        logger.info("Running test securely inside Docker container.")
        import os
        uid = os.getuid()
        gid = os.getgid()

        # Install dependencies and run tests in a single container invocation
        # because --rm ephemeral containers do not persist state across runs.
        combined_cmd = (
            "(pip install -e . > /tmp/install.log 2>&1) || true; "
            "(pip install -e '.[test]' >> /tmp/install.log 2>&1) || true; "
            "(pip install -e '.[dev]' >> /tmp/install.log 2>&1) || true; "
            "(pip install -r requirements.txt >> /tmp/install.log 2>&1) || true; "
            "(pip install -r requirements-dev.txt >> /tmp/install.log 2>&1) || true; "
            "(pip install pytest pytest-asyncio testfixtures mock factory-boy hypothesis >> /tmp/install.log 2>&1) || true; "
            + " ".join(cmd)
        )

        docker_cmd = [
            "docker", "run", "--rm",
            "--cpus", "1.0",
            "--memory", "1g",
            "-v", f"{cwd.resolve()}:/repo:Z",
            "-w", "/repo",
            "openforge-python:3.11",
            "bash", "-c", combined_cmd
        ]

        try:
            return subprocess.run(docker_cmd, capture_output=True, text=True, timeout=300.0)
        except subprocess.TimeoutExpired as e:
            logger.error("Sandbox test timed out after 300s.")
            # Return a fabricated CompletedProcess
            return subprocess.CompletedProcess(
                args=docker_cmd,
                returncode=124, # Standard timeout exit code
                stdout=e.stdout.decode('utf-8') if isinstance(e.stdout, bytes) else (e.stdout or ""),
                stderr="ERROR: Command timed out after 300s."
            )

def get_sandbox() -> SandboxRunner:
    """Returns a DockerSandbox if docker is running, else LocalSandbox."""
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, check=False)
        if res.returncode == 0:
            return DockerSandbox()
    except FileNotFoundError:
        pass
    return LocalSandbox()
