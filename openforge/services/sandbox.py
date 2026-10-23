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
        # Each install is tracked individually so failures are reported instead
        # of being silently swallowed with "|| true".
        combined_cmd = (
            "INSTALL_ERRORS=0; INSTALL_DIAGNOSTIC=''; "
            "if ! pip install -e . >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install -e .\"; fi; "
            "if ! pip install -e '.[test]' >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install -e .[test]\"; fi; "
            "if ! pip install -e '.[dev]' >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install -e .[dev]\"; fi; "
            "if [ -f requirements.txt ] && ! pip install -r requirements.txt >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install -r requirements.txt\"; fi; "
            "if [ -f requirements-dev.txt ] && ! pip install -r requirements-dev.txt >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install -r requirements-dev.txt\"; fi; "
            "if ! pip install pytest pytest-asyncio testfixtures mock factory-boy hypothesis >>/tmp/install.log 2>&1; then INSTALL_ERRORS=1; INSTALL_DIAGNOSTIC=\"${INSTALL_DIAGNOSTIC}\\nFAILED: pip install common test deps\"; fi; "
            "if [ \"$INSTALL_ERRORS\" = \"1\" ]; then "
            "  echo '=== DEPENDENCY INSTALLATION FAILED ===' >&2; "
            "  echo -e \"$INSTALL_DIAGNOSTIC\" >&2; "
            "  cat /tmp/install.log >&2; "
            "fi; "
            + 'exec "$@"'
        )

        docker_cmd = [
            "docker", "run", "--rm",
            "--cpus", "1.0",
            "--memory", "1g",
            "-v", f"{cwd.resolve()}:/repo:Z",
            "-w", "/repo",
            "openforge-python:3.11",
            "bash", "-c", combined_cmd, "bash", *cmd
        ]

        try:
            result = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=300.0)
            return result
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
    """Return DockerSandbox, failing closed when Docker is unavailable."""
    try:
        res = subprocess.run(["docker", "info"], capture_output=True, check=False)
        if res.returncode == 0:
            return DockerSandbox()
    except FileNotFoundError:
        pass
    raise RuntimeError(
        "Docker is unavailable. Refusing to execute repository code on the host."
    )
