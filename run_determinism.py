import json
import logging
import subprocess
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO)

repo_url = "https://github.com/scrapy/scrapy"
issue_url = "https://github.com/scrapy/scrapy/issues/1988"

def main():
    runs_stats = []
    
    for i in range(5):
        run_id = f"det-test-{int(time.time())}-{i}"
        logging.info(f"--- Determinism Run {i+1} / 5 (Run ID: {run_id}) ---")
        
        # 1. Inspect
        subprocess.run(["uv", "run", "openforge", "inspect", repo_url], check=True)
        # 2. Analyze
        subprocess.run(["uv", "run", "openforge", "analyze-issue", issue_url, "--run", run_id], check=True)
        # 3. Plan
        subprocess.run(["uv", "run", "openforge", "plan", issue_url, "--run", run_id], check=True)
        # 4. Code
        code = subprocess.run(["uv", "run", "openforge", "code", "--run", run_id, "--force"])
        
        run_dir = Path(f"runs/{run_id}")
        plan_json = run_dir / "plan.json"
        code_json = run_dir / "code.json"
        metrics_json = run_dir / "prompt_metrics.json"
        
        # Collect stats
        plan_data = json.loads(plan_json.read_text()) if plan_json.exists() else {}
        code_data = json.loads(code_json.read_text()) if code_json.exists() else {}
        
        stats = {
            "run_number": i + 1,
            "success": code.returncode == 0,
            "files_modified": plan_data.get("files_to_modify", []),
            "edits_count": len(code_data.get("edits", []))
        }
        runs_stats.append(stats)
        
    Path("determinism_results.json").write_text(json.dumps(runs_stats, indent=2))
    logging.info("Determinism test complete.")

if __name__ == "__main__":
    main()
