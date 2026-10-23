import json
from pathlib import Path
import os
os.environ["LLM_PROVIDER"] = "dahl"
os.environ["DAHL_API_KEY"] = "test"
from openforge.cli import _load_repository_context
from openforge.models import IssueFacts
from openforge.agents.planner import PlannerAgent
from openforge.config import Settings

run_dir = Path("runs/2026-07-23T15-29-23")
repo = _load_repository_context(run_dir / "repository.json", no_verify=True)
facts_raw = json.loads((run_dir / "issue_facts.json").read_text())
facts = IssueFacts(**facts_raw["data"])

settings = Settings.load()
planner = PlannerAgent(settings)
snippets = planner._read_mentioned_files(repo, facts)
prompt = planner._render_prompt(repo, facts, snippets)

def tokens(text): return len(text) // 4

print("Full Planner Prompt Tokens (~):", tokens(prompt))
