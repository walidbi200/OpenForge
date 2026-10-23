import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from openforge.agents.coder import CodingAgent, RepositoryContext
from openforge.config import Settings

def test_dependency_detection():
    agent = CodingAgent(Settings(agentrouter_api_key="test"))
    repo_context = RepositoryContext(
        repo="test/test",
        source_path="/tmp/repo",
        language="Python",
        framework="Flask",
        package_manager="pip",
        test_command="pytest",
        has_tests=True
    )
    
    with patch("openforge.agents.coder.get_sandbox") as mock_get_sandbox, \
         patch("subprocess.run") as mock_run:
        
        mock_sandbox = MagicMock()
        mock_get_sandbox.return_value = mock_sandbox
        
        # First call: run tests -> fails with ImportError
        proc_fail = MagicMock()
        proc_fail.returncode = 1
        proc_fail.stderr = "ImportError: No module named 'flask'"
        
        # Second call: install dependency -> succeeds
        proc_install = MagicMock()
        proc_install.returncode = 0
        proc_install.stderr = ""
        
        # Third call: run tests again -> succeeds
        proc_success = MagicMock()
        proc_success.returncode = 0
        proc_success.stdout = "PASSED"
        proc_success.stderr = ""
        
        mock_sandbox.run.side_effect = [proc_fail, proc_success]
        mock_run.side_effect = [proc_install]

        results = agent._run_tests(["pytest"], Path("/tmp/repo"), repo_context)

        assert len(results) == 1
        assert results[0].returncode == 0

        assert mock_sandbox.run.call_count == 2
        assert mock_run.call_count == 1
        # First call: pytest
        assert "pytest" in mock_sandbox.run.call_args_list[0][0][0]
        # Second call: pip install
        assert "pip" in mock_run.call_args_list[0][0][0]
