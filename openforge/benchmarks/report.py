"""Report generator for BenchmarkResults."""

import json
from pathlib import Path
from datetime import datetime, timezone

from openforge.benchmarks.runner import BenchmarkResults


class BenchmarkReporter:
    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir

    def generate_report(self, results: BenchmarkResults) -> Path:
        """Generate all report files and return the output directory path."""
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        out_dir = self.runs_dir / "benchmark" / timestamp
        out_dir.mkdir(parents=True, exist_ok=True)

        self._write_accuracy_json(out_dir, results)
        self._write_summary_json(out_dir, results)
        self._write_results_json(out_dir, results)
        self._write_report_md(out_dir, results)

        return out_dir

    def _write_accuracy_json(self, out_dir: Path, results: BenchmarkResults) -> None:
        data = {
            "overall_accuracy": round(results.overall_accuracy, 1),
            "repository_accuracy": {
                r.name: round(r.accuracy, 1) for r in results.repos
            },
            "field_accuracy": {
                k: round(v, 1) for k, v in results.field_accuracy.items()
            }
        }
        (out_dir / "accuracy.json").write_text(json.dumps(data, indent=2) + "\n")

    def _write_summary_json(self, out_dir: Path, results: BenchmarkResults) -> None:
        data = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "overall_accuracy": results.overall_accuracy,
            "perfect_repos_count": len(results.perfect_repos),
            "failed_repos_count": len(results.failed_repos),
            "total_repos": len(results.repos)
        }
        (out_dir / "summary.json").write_text(json.dumps(data, indent=2) + "\n")

    def _write_results_json(self, out_dir: Path, results: BenchmarkResults) -> None:
        data = []
        for r in results.repos:
            data.append({
                "name": r.name,
                "url": r.url,
                "accuracy": r.accuracy,
                "matches": r.matches,
                "expected": r.expected,
                "detected": r.detected
            })
        (out_dir / "results.json").write_text(json.dumps(data, indent=2) + "\n")

    def _write_report_md(self, out_dir: Path, results: BenchmarkResults) -> None:
        lines = [
            "# Repository Benchmark",
            "",
            f"Overall Accuracy: **{results.overall_accuracy:.1f}%**",
            "",
            "## Perfect Repositories",
            ""
        ]

        if results.perfect_repos:
            for repo in results.perfect_repos:
                lines.append(f"- {repo}")
        else:
            lines.append("*None*")

        lines.extend([
            "",
            "## Repositories with Failures",
            ""
        ])

        if not results.failed_repos:
            lines.append("*None*")
        else:
            for repo in results.failed_repos:
                lines.append(f"### {repo}")
                lines.append("")
                # Find the result object
                repo_result = next(r for r in results.repos if r.name == repo)
                for field, match in repo_result.matches.items():
                    if not match:
                        expected = repo_result.expected.get(field, "none")
                        detected = repo_result.detected.get(field)
                        if detected is None:
                            detected = "none"
                        lines.append(f"**{field}**")
                        lines.append(f"- Expected: {expected}")
                        lines.append(f"- Detected: {detected}")
                        lines.append("")

        lines.extend([
            "## Field Accuracy",
            ""
        ])
        for field, acc in sorted(results.field_accuracy.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"- **{field}**: {acc:.1f}%")
        
        lines.append("")

        (out_dir / "report.md").write_text("\n".join(lines))
