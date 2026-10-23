from pathlib import Path
import json
from openforge.cli import _load_repository_context
from openforge.agents.planner import ImplementationPlan
from openforge.agents.coder import CodingAgent
from openforge.config import Settings

run_dir = Path("runs/2026-07-23T15-29-23")
repo_context = _load_repository_context(run_dir / "repository.json", no_verify=True)
raw_plan = json.loads((run_dir / "plan.json").read_text())
plan = ImplementationPlan(**raw_plan.get("data", raw_plan))

settings = Settings(llm_provider="dahl", dahl_api_key="test")
coder = CodingAgent(settings)
repo_path = Path("workspace/scrapy-scrapy")
current_files = coder._read_files(repo_path, plan.files_to_modify)
prompt = coder._render_prompt(repo_context, plan, current_files)

print("Prompt length:", len(prompt))
print("Prompt tokens (~):", len(prompt) // 4)
Path("dump_prompt.txt").write_text(prompt)
