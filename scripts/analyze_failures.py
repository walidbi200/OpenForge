import json
import os
import re
from pathlib import Path

def classify_failure(val_dir: Path, run_id: str) -> str:
    summary_path = val_dir / "summary.md"
    run_dir = Path("runs") / run_id
    debug_path = run_dir / "coder_debug.json"
    stderr_path = val_dir / "stderr.log"
    test_path = val_dir / "test.log"

    if not summary_path.exists():
        return "Unknown"

    summary = summary_path.read_text(encoding="utf-8")
    
    if stderr_path.exists():
        stderr = stderr_path.read_text(encoding="utf-8")
        if "HTTP 429" in stderr or "All retry/failover attempts exhausted" in stderr:
            return "Rate limit"
        if "timeout" in stderr.lower():
            return "Timeout"
        if "Planner error: LLM returned non-JSON output" in stderr:
            return "Planner error (Invalid JSON)"
        if "Coder error: Coder LLM returned non-JSON output" in stderr:
            return "Coder error (Invalid JSON)"
            
    if (val_dir / "stdout.log").exists():
        stdout = (val_dir / "stdout.log").read_text(encoding="utf-8")
        if "Planner error: LLM returned non-JSON output" in stdout or "Planner error: LLM returned non-JSON output." in stdout:
            return "Planner error (Invalid JSON)"
        if "Coder error: Coder LLM returned non-JSON output" in stdout or "Coder error: Coder LLM returned non-JSON output." in stdout:
            return "Coder error (Invalid JSON)"
            
    if debug_path.exists():
        try:
            debug = json.loads(debug_path.read_text(encoding="utf-8"))
            diagnostics = debug.get("parser_diagnostics", {})
            reason = diagnostics.get("reason", "").lower()
            status = diagnostics.get("status", "")
            
            if status == "cannot_complete":
                if "context" in reason:
                    return "Context too large"
                if "file" in reason:
                    return "Wrong file selection"
                return "Planner error"
                
            rejected = diagnostics.get("rejected_edits", [])
            warnings = diagnostics.get("parser_warnings", [])
            
            if rejected or any("Search block not found" in w for w in warnings):
                return "Invalid SEARCH/REPLACE"
                
            raw = debug.get("raw_llm_response", "").strip()
            if not raw or raw == "{}":
                return "Empty LLM response"
                
        except Exception:
            pass
            
    # Check testing or syntax errors
    if "- **Test Status**: Failed" in summary or ("- **Tests Executed**: Yes" in summary and "- **Status**: Failed" in summary):
        if test_path.exists():
            test_log = test_path.read_text(encoding="utf-8")
            if "SyntaxError" in test_log or "IndentationError" in test_log:
                return "Syntax error"
        return "Test failure"
        
    if "- **Files Modified**: 0" in summary:
        return "Empty LLM response"

    return "Unknown"

def main():
    validation_dir = Path("validation")
    if not validation_dir.exists():
        print("No validation directory found.")
        return

    results = []
    
    for repo_owner in os.listdir(validation_dir):
        owner_dir = validation_dir / repo_owner
        if not owner_dir.is_dir() or repo_owner == "targets.json":
            continue
            
        for repo_name in os.listdir(owner_dir):
            repo_dir = owner_dir / repo_name
            if not repo_dir.is_dir():
                continue
                
            for issue_num in os.listdir(repo_dir):
                run_dir = repo_dir / issue_num
                if not run_dir.is_dir():
                    continue
                
                target = f"{repo_owner}/{repo_name}#{issue_num}"
                
                summary_path = run_dir / "summary.md"
                if not summary_path.exists():
                    continue
                
                summary = summary_path.read_text(encoding="utf-8")
            
            # Extract run ID from summary
            run_id = "unknown"
            for line in summary.split("\n"):
                if line.startswith("- **Run ID**:"):
                    run_id = line.split("**Run ID**:")[1].strip()
                    break
                    
            if "- **Status**: Success" in summary:
                continue
                
            classification = classify_failure(run_dir, run_id)
            results.append((target, run_id, classification))
        
    results.sort()
    
    out_lines = ["# Failure Analysis\n", "| Repository | Run ID | Classification |", "|---|---|---|"]
    for target, run_id, cls in results:
        out_lines.append(f"| {target} | {run_id} | {cls} |")
        
    out_dir = Path("release")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "failure_analysis.md"
    out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"Generated {out_path}")

if __name__ == "__main__":
    main()
