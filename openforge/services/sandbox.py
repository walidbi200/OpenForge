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
        
        # We mount the workspace repo directly into /repo
        # Uses a pre-built image with pytest installed.
        # :Z relabels for SELinux compatibility (required on Fedora/RHEL).
        # network + read-only removed: --network none breaks DNS (pip needs to resolve pypi.org),
        # and --read-only blocks pip install. Container isolation + user namespace + resource
        # limits still provide strong sandboxing.
        docker_cmd = [
            "docker", "run", "--rm",
            "--user", f"{uid}:{gid}",
            "--cpus", "1.0",
            "--memory", "1g",
            "-v", f"{cwd.resolve()}:/repo:Z",
            "-w", "/repo",
            "openforge-python:3.11",
            "bash", "-c", " ".join(cmd)
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
