import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from openforge.agents.planner import PlannerAgent
from openforge.config import Settings
from openforge.models import RepositoryContext, IssueFacts

def test_prompt_metrics_recorded(tmp_path: Path):
    agent = PlannerAgent(Settings(agentrouter_api_key="test"))
    agent._prompt_template = "## Inputs\n## Instructions\n"
    agent._parse_plan = MagicMock()
    
    repo_context = RepositoryContext(repo="test/test", source_path=str(tmp_path), language="Python", framework="None", package_manager="pip", test_command="pytest", has_tests=True)
    issue_facts = IssueFacts(issue_number=1, issue_title="Test", issue_body="Test", issue_type="bug", has_traceback=False, has_reproduction_steps=False, mentioned_files=[], checklist=[])
    
    with patch("openforge.agents.planner.call_llm") as mock_call_llm:
        def side_effect(prompt, settings, timeout, metrics_callback, **kwargs):
            metrics_callback({"prompt_tokens": 100})
            return "{}"
        
        mock_call_llm.side_effect = side_effect
        
        agent.plan(repo_context, issue_facts, run_dir=tmp_path)
        
    metrics_file = tmp_path / "prompt_metrics.json"
    assert metrics_file.exists()
    
    data = json.loads(metrics_file.read_text())
    assert len(data) == 1
    assert data[0]["prompt_tokens"] == 100
    assert data[0]["agent"] == "PlannerAgent"
    assert "section_breakdown" in data[0]
