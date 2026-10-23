"""Benchmark runner for RepositoryAnalyzer."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openforge.services.repository_analyzer import RepositoryAnalyzer
from openforge.models import RepositoryContext


@dataclass
class RepoResult:
    name: str
    url: str
    expected: dict[str, str]
    detected: dict[str, str | None]
    matches: dict[str, bool]
    correct_count: int
    total_count: int
    accuracy: float


@dataclass
class BenchmarkResults:
    repos: list[RepoResult]
    overall_accuracy: float
    field_accuracy: dict[str, float]
    perfect_repos: list[str]
    failed_repos: list[str]


class BenchmarkRunner:
    """Runs accuracy benchmarks on RepositoryAnalyzer."""

    def __init__(self, workspace_dir: Path, json_path: Path):
        self.workspace_dir = workspace_dir
        self.json_path = json_path
        self.analyzer = RepositoryAnalyzer(workspace_dir)

    def load_repositories(self) -> list[dict[str, Any]]:
        with open(self.json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("repositories", [])

    def run(self, repo_names: list[str] | None = None, update: bool = False) -> BenchmarkResults:
        repos = self.load_repositories()
        if repo_names:
            repos = [r for r in repos if r["name"] in repo_names]

        results = []
        fields_to_check = [
            "language",
            "framework",
            "package_manager",
            "formatter",
            "linter",
            "test_command",
            "ci"
        ]

        field_correct = {f: 0 for f in fields_to_check}
        field_total = {f: 0 for f in fields_to_check}
        total_correct = 0
        total_fields = 0

        perfect_repos = []
        failed_repos = []

        for repo_def in repos:
            name = repo_def["name"]
            url = repo_def["url"]
            expected = repo_def["expected"]

            # Use resolve to get local path. If update=True, we might need to delete it first.
            # But the requirement says "Only --update should delete/reclone". 
            # We can let `cli.py` handle deleting the directory before calling `runner.run()`, 
            # or handle it here.
            # Wait, `analyzer.resolve(url)` will clone it if it doesn't exist, and reuse if it does.
            # Let's handle `--update` deletion here.
            local_path = self.workspace_dir / url.split("/")[-1]
            if update and local_path.exists():
                import shutil
                shutil.rmtree(local_path)

            try:
                local_path, repo_label = self.analyzer.resolve(url)
                context: RepositoryContext = self.analyzer.analyze(local_path, repo_label)
                
                # Convert context to dict for easier comparison
                context_dict = context.model_dump()
            except Exception as e:
                # If analysis fails, treat all fields as missing/None
                context_dict = {}

            matches = {}
            repo_correct = 0

            for field in fields_to_check:
                expected_val = expected.get(field, "none")
                # Normalize expected value
                if expected_val == "none":
                    expected_val = None
                
                detected_val = context_dict.get(field)
                
                if isinstance(detected_val, str) and detected_val.lower().strip() in ("none", "", "n/a", "null"):
                    detected_val = None
                
                # We do a case-insensitive, strip matching just to be robust, or exact matching?
                # The user expected e.g., "language": "Python" and analyzer outputs "Python".
                # Let's do a relaxed match: lowercase and strip.
                match = False
                if expected_val is None and detected_val is None:
                    match = True
                elif expected_val is not None and detected_val is not None:
                    match = str(expected_val).lower().strip() == str(detected_val).lower().strip()
                
                matches[field] = match
                field_total[field] += 1
                if match:
                    field_correct[field] += 1
                    repo_correct += 1

            repo_total = len(fields_to_check)
            repo_accuracy = (repo_correct / repo_total) * 100 if repo_total > 0 else 0.0

            total_correct += repo_correct
            total_fields += repo_total

            if repo_correct == repo_total:
                perfect_repos.append(name)
            else:
                failed_repos.append(name)

            detected = {f: context_dict.get(f) for f in fields_to_check}

            results.append(
                RepoResult(
                    name=name,
                    url=url,
                    expected=expected,
                    detected=detected,
                    matches=matches,
                    correct_count=repo_correct,
                    total_count=repo_total,
                    accuracy=repo_accuracy,
                )
            )

        overall_accuracy = (total_correct / total_fields) * 100 if total_fields > 0 else 0.0
        field_accuracy = {
            f: (field_correct[f] / field_total[f]) * 100 if field_total[f] > 0 else 0.0
            for f in fields_to_check
        }

        return BenchmarkResults(
            repos=results,
            overall_accuracy=overall_accuracy,
            field_accuracy=field_accuracy,
            perfect_repos=perfect_repos,
            failed_repos=failed_repos,
        )
