import json
import logging
import subprocess
import sys
import time
import shutil
from pathlib import Path

# Shared patch helper — single source of truth for generated patches.
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from openforge.patch import get_patch, evaluate_test_results

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def get_latest_run_id() -> str:
    runs_dir = Path("runs")
    if not runs_dir.exists():
        raise RuntimeError("No runs/ directory found.")

    runs = [d for d in runs_dir.iterdir() if d.is_dir() and d.name != "validation"]
    if not runs:
        raise RuntimeError("No run directories found.")

    runs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    return runs[0].name

def run_command(cmd: list[str]) -> tuple[int, str, str, float]:
    logger.info("Executing: %s", " ".join(cmd))
    start_t = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True)
    end_t = time.time()

    if result.returncode != 0:
        logger.error("Command failed with exit code %d:\n%s\n%s", result.returncode, result.stdout, result.stderr)

    return result.returncode, result.stdout, result.stderr, end_t - start_t

def extract_json_repair_events(stderr: str) -> int:
    return stderr.count("JSON repair successful")

def get_git_diff(repo_path: str) -> str:
    """Return the staged diff for *repo_path*.

    Uses ``git diff --staged`` to match the Coder's workflow.
    Previously used ``git diff HEAD~1`` which fails on shallow clones.
    """
    from openforge.patch import get_staged_diff
    return get_staged_diff(repo_path)

def get_git_branch(repo_path: str) -> str:
    res = subprocess.run(["git", "branch", "--show-current"], cwd=repo_path, capture_output=True, text=True)
    return res.stdout.strip()

def main():
    targets_file = Path("validation/targets.json")
    if not targets_file.exists():
        logger.error("Missing validation/targets.json")
        sys.exit(1)

    targets = json.loads(targets_file.read_text()).get("targets", [])

    for target in targets:
        repo = target["repo"]
        issue_number = target["issue"]
        repo_url = f"https://github.com/{repo}"
        issue_url = f"{repo_url}/issues/{issue_number}"
        
        logger.info("=" * 60)
        logger.info("Validating %s#%s", repo, issue_number)
        logger.info("=" * 60)

        # Output dir
        dest_dir = Path(f"validation/{repo}/{issue_number}")
        dest_dir.mkdir(parents=True, exist_ok=True)

        full_stdout = ""
        full_stderr = ""
        total_time = 0.0

        def exec_step(cmd):
            nonlocal full_stdout, full_stderr, total_time
            code, out, err, t = run_command(cmd)
            full_stdout += f"\n--- CMD: {' '.join(cmd)} ---\n{out}"
            full_stderr += f"\n--- CMD: {' '.join(cmd)} ---\n{err}"
            total_time += t
            return code, out, err, t

        # 1. Inspect
        code, out, err, t_inspect = exec_step(["uv", "run", "openforge", "inspect", repo_url])
        if code != 0:
            (dest_dir / "stdout.log").write_text(full_stdout)
            (dest_dir / "stderr.log").write_text(full_stderr)
            (dest_dir / "summary.md").write_text(f"Failed at inspect step.\nExit code: {code}\nTime: {total_time:.2f}s")
            continue
            
        run_id = get_latest_run_id()
        logger.info("Generated Run ID: %s", run_id)

        # 2. Analyze Issue
        code, out, err, t_analyze = exec_step(["uv", "run", "openforge", "analyze-issue", issue_url, "--run", run_id])
        if code != 0:
            (dest_dir / "stdout.log").write_text(full_stdout)
            (dest_dir / "stderr.log").write_text(full_stderr)
            (dest_dir / "summary.md").write_text(f"Failed at analyze-issue step.\nExit code: {code}\nTime: {total_time:.2f}s")
            continue

        # 3. Plan
        code, out, err, t_plan = exec_step(["uv", "run", "openforge", "plan", issue_url, "--run", run_id])
        if code != 0:
            (dest_dir / "stdout.log").write_text(full_stdout)
            (dest_dir / "stderr.log").write_text(full_stderr)
            (dest_dir / "summary.md").write_text(f"Failed at plan step.\nExit code: {code}\nTime: {total_time:.2f}s")
            continue

        # 4. Code
        code, out, err, t_code = exec_step(["uv", "run", "openforge", "code", "--run", run_id, "--force"])

        # Write stdout/stderr
        (dest_dir / "stdout.log").write_text(full_stdout)
        (dest_dir / "stderr.log").write_text(full_stderr)

        # Copy artifacts
        run_dir = Path(f"runs/{run_id}")
        for artifact in ["repository.json", "issue_facts.json", "plan.json", "code.json", "manifest.json", "logs.txt", "prompt_metrics.json"]:
            src = run_dir / artifact
            if src.exists():
                shutil.copy2(src, dest_dir / artifact)

        # Try to capture LLM raw failed outputs if present
        if Path("failed_coder_raw.json").exists():
            shutil.copy2("failed_coder_raw.json", dest_dir / "llm_response_raw.json")
        if Path("failed_coder_repaired.json").exists():
            shutil.copy2("failed_coder_repaired.json", dest_dir / "llm_response_repaired.json")

        # Gather metrics
        repair_events = extract_json_repair_events(full_stderr)
        
        # Repo path to get git diff
        try:
            repo_data = json.loads((run_dir / "repository.json").read_text()).get("data", {})
            repo_path = repo_data.get("source_path")
            if not repo_path:
                raise ValueError("Missing source_path in repository.json data")
            branch_name = get_git_branch(repo_path)
            git_diff = get_git_diff(repo_path)
            (dest_dir / "git.diff").write_text(git_diff)
        except Exception as e:
            branch_name = f"Unknown ({e})"
            git_diff = ""
            
        files_modified = len(git_diff.split("diff --git")) - 1 if git_diff else 0

        # Try to extract test logs from coder output
        test_log = "Test log not extracted."
        if "Running tests..." in full_stdout:
            test_log = full_stdout.split("Running tests...")[-1]
        elif "Running tests..." in full_stderr:
            test_log = full_stderr.split("Running tests...")[-1]

        # Extract metrics
        provider = "unknown"
        model = "unknown"
        total_tokens = 0
        prompt_metrics = dest_dir / "prompt_metrics.json"
        if prompt_metrics.exists():
            try:
                metrics_data = json.loads(prompt_metrics.read_text())
                if metrics_data:
                    provider = metrics_data[-1].get("provider_used", "unknown")
                    model = metrics_data[-1].get("model", "unknown")
                    total_tokens = sum(m.get("total_tokens", 0) for m in metrics_data)
            except Exception:
                pass

        (dest_dir / "test.log").write_text(test_log)

        # Structured test evaluation from code.json (single source of truth)
        code_json_path = dest_dir / "code.json"
        structured_all_passed, structured_test_results = evaluate_test_results(code_json_path)
        tests_in_code_json = bool(structured_test_results)

        if tests_in_code_json:
            # Prefer structured data: every test_result.passed must be True
            test_status = "Passed" if structured_all_passed else "Failed"
        else:
            # Fallback: no test results in code.json — treat as failed
            test_status = "Failed"

        # Strict success criteria
        overall_success = (
            code == 0
            and files_modified > 0
            and git_diff != ""
            and test_status == "Passed"
        )
        status_str = "Success" if overall_success else "Failed"

        # Generate summary
        summary = f"""# Validation Summary: {repo}#{issue_number}

- **Run ID**: {run_id}
- **Status**: {status_str}
- **Exit Code**: {code}
- **Pipeline Duration**: {total_time:.2f}s
- **Provider**: {provider}
- **Model**: {model}
- **Total Tokens**: {total_tokens}
- **Branch Name**: {branch_name}
- **Files Modified**: {files_modified}
- **JSON Repair Events**: {repair_events}
- **Tests Executed**: {"Yes" if tests_in_code_json else "No"}
- **Test Status**: {test_status}
- **Reviewable Patch**: {"Yes" if git_diff and files_modified > 0 else "No"}

## Failure Diagnosis
*{"None" if overall_success else "Review `stderr.log` and `test.log` for root causes."}*

## Execution Times
- **Inspect**: {t_inspect:.2f}s
- **Analyze Issue**: {t_analyze:.2f}s
- **Plan**: {t_plan:.2f}s
- **Code**: {t_code:.2f}s
"""
        (dest_dir / "summary.md").write_text(summary)
        logger.info("Validation for %s complete.", repo)

if __name__ == "__main__":
    main()
