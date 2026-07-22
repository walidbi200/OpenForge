"""Shared pytest fixtures and session-level validation report generation.

The ``validation_report`` fixture (session-scoped) accumulates per-entry
results during the catalog test run.  At session teardown it writes a
Markdown report to ``runs/validation/report.md``.

Only catalog tests need to request ``validation_report``; the existing
unit tests in test_repository_analyzer.py are unaffected.
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from tests.catalog import CATALOG, EXPECTED_FIELDS, CatalogEntry, Gap

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class FieldResult:
    name: str
    expected: Optional[Any]
    actual: Optional[Any]

    @property
    def passed(self) -> bool:
        return self.expected == self.actual


@dataclass
class EntryResult:
    entry: CatalogEntry
    field_results: list[FieldResult]

    @property
    def passed(self) -> bool:
        return all(f.passed for f in self.field_results)

    @property
    def failed_fields(self) -> list[FieldResult]:
        return [f for f in self.field_results if not f.passed]


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

_PRIORITY = {"High": "🔴", "Medium": "🟡", "Low": "🟢"}

# Fields where a gap is "High" priority (missing for whole language families)
_HIGH_PRIORITY_FIELDS = {"test_command", "package_manager"}


def _render_report(results: list[EntryResult]) -> str:
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(results)
    fully_passed = sum(1 for r in results if r.passed)

    # Per-field accuracy
    field_correct: dict[str, int] = {f: 0 for f in EXPECTED_FIELDS}
    field_total: dict[str, int] = {f: 0 for f in EXPECTED_FIELDS}
    for r in results:
        for fr in r.field_results:
            field_total[fr.name] += 1
            if fr.passed:
                field_correct[fr.name] += 1

    total_fields = sum(field_total.values())
    correct_fields = sum(field_correct.values())

    lines: list[str] = []

    lines.append("# RepositoryAnalyzer Validation Report\n")
    lines.append(f"**Generated:** {now}  ")
    lines.append(f"**Module:** `openforge.services.repository_analyzer.RepositoryAnalyzer`\n")

    lines.append("## Executive Summary\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Catalog entries | {total} |")
    lines.append(
        f"| Fully passing entries | **{fully_passed}/{total}** "
        f"({100*fully_passed//total}%) |"
    )
    lines.append(
        f"| Overall field accuracy | **{correct_fields}/{total_fields}** "
        f"({100*correct_fields//total_fields}%) |"
    )

    # Count gaps
    all_gaps: list[tuple[str, Gap]] = [
        (r.entry.id, g) for r in results for g in r.entry.gaps
    ]
    lines.append(f"| Known improvement gaps | {len(all_gaps)} |")
    lines.append("")

    # Per-field accuracy table
    lines.append("## Per-Field Accuracy\n")
    lines.append("| Field | Correct | Total | Accuracy |")
    lines.append("|---|---|---|---|")
    for f in EXPECTED_FIELDS:
        c, t = field_correct[f], field_total[f]
        pct = f"{100*c//t}%" if t else "—"
        bar = "✅" if c == t else ("⚠️" if c >= t * 0.8 else "❌")
        lines.append(f"| `{f}` | {c} | {t} | {bar} {pct} |")
    lines.append("")

    # Results by entry
    lines.append("## Results by Entry\n")
    header_fields = " | ".join(f"`{f}`" for f in EXPECTED_FIELDS)
    lines.append(f"| ID | Group | {header_fields} | Status |")
    lines.append("|---|---|" + "|".join(["---"] * len(EXPECTED_FIELDS)) + "|---|")
    for r in results:
        cells = []
        for fr in r.field_results:
            if fr.passed:
                cells.append("✅")
            else:
                cells.append(f"❌ `{fr.actual!r}`")
        status = "✅ PASS" if r.passed else "❌ FAIL"
        cells_str = " | ".join(cells)
        lines.append(f"| `{r.entry.id}` | {r.entry.language_tag} | {cells_str} | {status} |")
    lines.append("")

    # Failure details (if any)
    failures = [r for r in results if not r.passed]
    if failures:
        lines.append("## ❌ Failure Details\n")
        for r in failures:
            lines.append(f"### `{r.entry.id}`\n")
            lines.append(f"> {r.entry.description}\n")
            lines.append("| Field | Expected | Actual |")
            lines.append("|---|---|---|")
            for fr in r.failed_fields:
                lines.append(f"| `{fr.name}` | `{fr.expected!r}` | `{fr.actual!r}` |")
            lines.append("")

    # Gaps and recommendations
    lines.append("## Recommended Heuristic Improvements\n")
    lines.append(
        "> These are gaps documented in the catalog where the analyzer's current output "
        "differs from the *ideal* output. They are **not test failures** — the tests "
        "assert current behaviour. Implementing these would unlock full support for the "
        "affected ecosystems.\n"
    )

    # Group gaps by field
    gaps_by_field: dict[str, list[tuple[str, Gap]]] = {}
    for entry_id, gap in all_gaps:
        gaps_by_field.setdefault(gap.field, []).append((entry_id, gap))

    for f in EXPECTED_FIELDS:
        if f not in gaps_by_field:
            continue
        priority = "High" if f in _HIGH_PRIORITY_FIELDS else "Medium"
        icon = _PRIORITY[priority]
        lines.append(f"### {icon} `{f}` ({priority} priority)\n")
        for entry_id, gap in gaps_by_field[f]:
            lines.append(f"**Entry `{entry_id}`** → recommended `{gap.recommended_value!r}`")
            lines.append(f": {gap.recommendation}\n")

    # Edge case summary
    edge_entries = [r for r in results if r.entry.language_tag == "Edge Case"]
    if edge_entries:
        lines.append("## Edge Cases\n")
        for r in edge_entries:
            lines.append(f"### `{r.entry.id}`\n")
            lines.append(f"**Status:** {'✅ PASS' if r.passed else '❌ FAIL'}  ")
            lines.append(f"**Description:** {r.entry.description}  ")
            if r.entry.notes:
                lines.append(f"\n{r.entry.notes}\n")
            if r.entry.gaps:
                lines.append("**Gaps identified:**")
                for g in r.entry.gaps:
                    lines.append(f"- `{g.field}` → `{g.recommended_value!r}`: {g.recommendation}")
            lines.append("")

    lines.append("---")
    lines.append(
        "*Report generated automatically by `tests/test_catalog_validation.py`. "
        "Re-run `uv pip install -e '.[dev]' && uv run python -m pytest tests/test_catalog_validation.py -v` "
        "to refresh.*"
    )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Module-level store (avoids fixture-scope gymnastics for the session hook)
# ---------------------------------------------------------------------------

_report_store: list[EntryResult] = []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def validation_report() -> list[EntryResult]:
    """Session-scoped list that tests append EntryResult objects to."""
    return _report_store


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Write the Markdown report once all tests have finished."""
    if not _report_store:
        return

    out = Path("runs/validation")
    out.mkdir(parents=True, exist_ok=True)
    report_path = out / "report.md"
    report_path.write_text(_render_report(_report_store))
    # Print a one-liner so the user knows where to find it.
    print(f"\n  📄 Validation report → {report_path.resolve()}")


# ---------------------------------------------------------------------------
# Fixture helper: materialise a catalog entry as files on disk
# ---------------------------------------------------------------------------


def build_fixture_repo(base: Path, entry: CatalogEntry) -> Path:
    """Create a temporary directory tree matching the catalog entry's ``files``."""
    repo = base / entry.id
    repo.mkdir(parents=True, exist_ok=True)
    for rel, content in entry.files.items():
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo
