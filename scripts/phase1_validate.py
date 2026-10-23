#!/usr/bin/env python3
"""Phase 1 validation — deterministic test matrix with hand-verified real GitHub URLs.

Usage:
    GITHUB_TOKEN=xxx uv run python scripts/phase1_validate.py

Every URL below was verified against the GitHub API before inclusion.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openforge.services.github import GitHubService, IssueNotActionableError

# Hand-verified real URLs.  We check each one against the live GitHub API.
TEST_CASES = [
    {
        "id": "open_unassigned_bug",
        "description": "Open, unassigned, bug label — should ALLOW",
        "url": "https://github.com/sanic-org/sanic/issues/3176",
        "expected": "ALLOW",
    },
    {
        "id": "open_unassigned_bug_2",
        "description": "Open, unassigned, bug label — should ALLOW",
        "url": "https://github.com/sanic-org/sanic/issues/3175",
        "expected": "ALLOW",
    },
    {
        "id": "closed_completed",
        "description": "Closed issue (completed) — should REJECT",
        "url": "https://github.com/django/django/issues/1",
        "expected": "REJECT",
    },
    {
        "id": "merged_pr",
        "description": "Merged pull request — should REJECT",
        "url": "https://github.com/pallets/flask/pull/6013",
        "expected": "REJECT",
    },
    {
        "id": "closed_not_planned",
        "description": "Closed, state_reason=not_planned — should REJECT",
        "url": "https://github.com/pallets/flask/issues/6092",
        "expected": "REJECT",
    },
    {
        "id": "open_pr",
        "description": "Open pull request — should REJECT",
        "url": "https://github.com/encode/httpx/pull/3785",
        "expected": "REJECT",
    },
    {
        "id": "open_assigned_bug",
        "description": "Open, assigned, bug label — should ALLOW",
        "url": "https://github.com/python/cpython/issues/154667",
        "expected": "ALLOW",
    },
]


def _test_one(svc: GitHubService, case: dict) -> dict:
    url = case["url"]
    expected = case["expected"]
    try:
        owner, repo, number = GitHubService.parse_issue_url(url)
        raw = svc.fetch_issue(owner, repo, number)
        actual = "ALLOW"
        state = raw.get("state", "unknown")
        title = raw.get("title", "")[:80]
        detail = f"state={state}, title={title}"
    except IssueNotActionableError as exc:
        actual = "REJECT"
        detail = str(exc)
    except Exception as exc:
        actual = f"ERROR ({type(exc).__name__})"
        detail = str(exc)

    should_pass = expected == "ALLOW"
    passed = (
        (should_pass and actual == "ALLOW")
        or (not should_pass and actual == "REJECT")
    )
    return {
        "id": case["id"],
        "description": case["description"],
        "url": url,
        "expected": expected,
        "actual": actual,
        "detail": detail,
        "pass": passed,
    }


def main() -> None:
    token = os.environ.get("GITHUB_TOKEN")
    svc = GitHubService(token=token)

    results = []
    for case in TEST_CASES:
        results.append(_test_one(svc, case))

    # Console report
    print("=" * 80)
    print(f"PHASE 1 — ISSUE VALIDATION LAYER VERIFICATION ({datetime.now(timezone.utc).isoformat()})")
    print("=" * 80)

    all_pass = True
    passed = failed = 0
    for r in results:
        ok = r["pass"]
        status = "✅ PASS" if ok else "🔴 FAIL"
        if ok:
            passed += 1
        else:
            failed += 1
            all_pass = False
        print(f"\n{status} — [{r['id']}] {r['description']}")
        print(f"  URL:      {r['url']}")
        print(f"  Expected: {r['expected']}  |  Actual: {r['actual']}")
        print(f"  Detail:   {r['detail'][:160]}")

    print("\n" + "-" * 80)
    print(f"Summary: {passed} passed, {failed} failed, {len(results)} total")
    if all_pass:
        print("ALL TESTS PASSED ✅")
    else:
        print("SOME TESTS FAILED — fix the validation layer before continuing.")
    print("=" * 80)

    # JSON dump
    out_dir = Path(__file__).resolve().parent.parent / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "phase1_validation_results.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nJSON report: {out_path}")


if __name__ == "__main__":
    main()
