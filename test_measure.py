import json
from pathlib import Path
import os
os.environ["LLM_PROVIDER"] = "dahl"
os.environ["DAHL_API_KEY"] = "test"
from openforge.cli import _load_repository_context
from openforge.agents.planner import ImplementationPlan
from openforge.agents.coder import CodingAgent
from openforge.config import Settings

run_dir = Path("runs/2026-07-23T15-29-23")
repo = _load_repository_context(run_dir / "repository.json", no_verify=True)
plan_raw = json.loads((run_dir / "plan.json").read_text())
plan = ImplementationPlan(**plan_raw["data"])

settings = Settings.load()
coder = CodingAgent(settings)
repo_path = Path("workspace/scrapy-scrapy")
current_files = coder._read_files(repo_path, plan.files_to_modify)

file_blocks = coder._render_file_blocks(current_files)
steps_text = "\n".join(f"{i}. {step}" for i, step in enumerate(plan.implementation_steps, 1))

def tokens(text): return len(text) // 4

print("Language/Framework/Test Command:", tokens(f"Language: {repo.language}\nFramework: {repo.framework}\nTest Command: {repo.test_command}"))
print("Goal:", tokens(plan.goal))
print("Steps:", tokens(steps_text))
print("File blocks:", tokens(file_blocks))
print("Full Prompt:", tokens(coder._render_prompt(repo, plan, current_files)))
