"""Determinism test for OpenForge.

Runs the full pipeline (analyze-issue -> plan -> code) on scrapy/scrapy#1988
multiple times and compares the generated patches.

Uses ``openforge.patch.get_patch`` — the single source of truth for
computing generated patches.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# Ensure the openforge package is importable.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openforge.patch import get_patch

ISSUE_URL = "https://github.com/scrapy/scrapy/issues/1988"
NUM_RUNS = 10
WORKSPACE_DIR = "workspace_det"


def run_pipeline() -> dict:
    """Execute one full pipeline run and return structured results."""
    start = time.time()
    env = {**os.environ, "OPENFORGE_WORKSPACE_DIR": WORKSPACE_DIR}

    # 1. Analyze issue
    res = subprocess.run(
        ["uv", "run", "openforge", "analyze-issue", ISSUE_URL],
        capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        raise RuntimeError(f"analyze-issue failed (exit {res.returncode}):\n{res.stderr}")

    # Extract Run ID
    match = re.search(
        r"([\d]{4}-[\d]{2}-[\d]{2}T[\d]{2}-[\d]{2}-[\d]{2})",
        res.stdout + res.stderr,
    )
    if not match:
        raise RuntimeError(f"Could not find Run ID in output:\n{res.stdout}\n{res.stderr}")
    run_id = match.group(1)
    latest_run = Path("runs") / run_id

    # 2. Plan
    res = subprocess.run(
        ["uv", "run", "openforge", "plan", ISSUE_URL, "--run", run_id],
        capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        raise RuntimeError(f"plan failed (exit {res.returncode}):\n{res.stderr}")

    # 3. Code
    res = subprocess.run(
        ["uv", "run", "openforge", "code", "--run", run_id, "--force"],
        capture_output=True, text=True, env=env,
    )
    if res.returncode != 0:
        raise RuntimeError(f"code failed (exit {res.returncode}):\n{res.stderr}")

    runtime = time.time() - start

    # 4. Read repo path
    repo_json = latest_run / "repository.json"
    repo_data = json.loads(repo_json.read_text())
    repo_path = repo_data.get("data", {}).get("source_path")
    if not repo_path:
        raise RuntimeError("No source_path in repository.json")

    # 5. Get patch via shared helper (git diff --staged)
    patch = get_patch(repo_path)
    if patch.is_empty:
        raise RuntimeError(
            "Generated patch is empty! "
            f"(files_modified={patch.files_modified}, diff_len={len(patch.diff)})"
        )

    # 6. Read metrics
    metrics_path = latest_run / "prompt_metrics.json"
    metrics = json.loads(metrics_path.read_text()) if metrics_path.exists() else []

    return {
        "run_id": run_id,
        "runtime": runtime,
        "metrics": metrics,
        "diff": patch.diff,
        "diff_hash": patch.diff_hash,
        "files_modified": patch.files_modified,
    }


def main() -> None:
    print(f"Starting Determinism Test ({NUM_RUNS} runs on scrapy/scrapy#1988)")
    results = []

    for i in range(NUM_RUNS):
        print(f"Run {i + 1}/{NUM_RUNS}...")
        try:
            res = run_pipeline()
            results.append(res)
            print(
                f"  Runtime: {res['runtime']:.2f}s | "
                f"Hash: {res['diff_hash'][:12]} | "
                f"Files: {res['files_modified']}"
            )
        except Exception as e:
            print(f"  FATAL: {e}")
            break
        finally:
            workspace_repo = Path(WORKSPACE_DIR)
            if workspace_repo.exists():
                shutil.rmtree(workspace_repo, ignore_errors=True)

    # Report
    print("\n=== Determinism Report ===")
    print(f"Total Runs Successfully Completed: {len(results)}")

    if results:
        hashes = [r["diff_hash"] for r in results]
        unique = set(hashes)
        print(f"Identical Patches: {len(results) - len(unique) + 1} / {len(results)}")
        if len(unique) > 1:
            print("WARNING: Nondeterminism detected — patches differ across runs.")

        runtimes = [r["runtime"] for r in results]
        print(f"Avg Runtime: {sum(runtimes) / len(runtimes):.2f}s")
        print(f"Min Runtime: {min(runtimes):.2f}s")
        print(f"Max Runtime: {max(runtimes):.2f}s")

        total_tokens = [
            sum(m.get("total_tokens", 0) for m in r["metrics"]) for r in results
        ]
        print(f"Avg Total Tokens: {sum(total_tokens) / len(total_tokens):.0f}")

    Path("determinism_report.json").write_text(json.dumps(results, indent=2))
    print("\nReport saved to determinism_report.json")


if __name__ == "__main__":
    main()
