"""GitHub REST API client.

Deterministic service layer — no LLM, no agent logic.

Responsibilities:
- Parse GitHub issue URLs to (owner, repo, number)
- Fetch raw issue data from the GitHub API

GitHub token is optional for public repos (60 req/hr unauthenticated).
Set GITHUB_TOKEN in the environment for higher rate limits and private repos.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"

# Matches both issue and PR URLs:
#   https://github.com/owner/repo/issues/123
#   https://github.com/owner/repo/pull/123
_ISSUE_URL_RE = re.compile(
    r"github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)"
    r"/(?:issues|pull)/(?P<number>\d+)"
)


class IssueNotActionableError(RuntimeError):
    """Raised when a GitHub issue is closed, is a PR, or already has a merged fix."""


class GitHubService:
    """Thin wrapper around the GitHub REST API.

    Usage::

        svc = GitHubService(token=settings.github_token)
        owner, repo, number = GitHubService.parse_issue_url(url)
        raw = svc.fetch_issue(owner, repo, number)
        title = raw["title"]
        body  = raw["body"] or ""
        labels = [l["name"] for l in raw.get("labels", [])]
    """

    def __init__(self, token: str | None = None, timeout: float = 15.0) -> None:
        self._token = token
        self._timeout = timeout
        if not token:
            logger.warning(
                "GITHUB_TOKEN not set — using unauthenticated GitHub API "
                "(60 req/hr limit). Public repos still work."
            )

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_issue(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Return the raw GitHub API response dict for one issue.

        Raises:
            IssueNotActionableError – issue is closed, is a PR, or has a merged fix.
            PermissionError         – 403 (rate-limited or private repo without token)
            RuntimeError            – any other HTTP error (including 404)
        """
        url = f"{_API_BASE}/repos/{owner}/{repo}/issues/{number}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"

        logger.debug("Fetching %s", url)
        try:
            response = httpx.get(url, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException as exc:
            raise RuntimeError(f"GitHub API timed out fetching issue #{number}: {exc}") from exc

        if response.status_code == 403:
            raise PermissionError(
                f"GitHub API returned 403 for {owner}/{repo}#{number}. "
                "Set GITHUB_TOKEN to avoid rate limits or access private repos."
            )

        if not response.is_success:
            raise RuntimeError(
                f"GitHub API returned {response.status_code} for "
                f"{owner}/{repo}#{number}: {response.text[:200]}"
            )

        raw = response.json()
        self._assert_actionable(owner, repo, number, raw, headers)
        return raw

    def _assert_actionable(
        self,
        owner: str,
        repo: str,
        number: int,
        raw: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """Validate that the fetched issue is actually open and unaddressed."""
        state: str = raw.get("state", "")
        state_reason: str | None = raw.get("state_reason")

        if state != "open":
            reason_fragment = f" (reason: {state_reason})" if state_reason else ""
            raise IssueNotActionableError(
                f"Issue {owner}/{repo}#{number} is not open — state='{state}'{reason_fragment}."
            )

        if "pull_request" in raw:
            raise IssueNotActionableError(
                f"{owner}/{repo}#{number} is a pull request, not an issue."
            )

        # Check the timeline for a cross-referenced / closed event that links
        # to an already-merged pull request.
        timeline_url = raw.get("timeline_url")
        if timeline_url:
            self._check_timeline_for_merged_fix(timeline_url, owner, repo, number, headers)

    def _check_timeline_for_merged_fix(
        self,
        timeline_url: str,
        owner: str,
        repo: str,
        number: int,
        headers: dict[str, str],
    ) -> None:
        """Scan timeline events for a merged PR that closes this issue."""
        try:
            resp = httpx.get(timeline_url, headers=headers, timeout=self._timeout)
        except httpx.TimeoutException:
            return  # degrade gracefully on timeout

        if not resp.is_success:
            return  # degrade gracefully on error

        events = resp.json()
        if not isinstance(events, list):
            return

        for event in events:
            if event.get("event") not in ("cross-referenced", "closed"):
                continue
            source = event.get("source", {})
            pr = source.get("issue", {}) if isinstance(source, dict) else {}
            if not pr:
                pr = event.get("pull_request", {})
            if not pr:
                # Timeline "closed" events may carry the PR directly in the payload.
                pr = event.get("source", {}).get("pull_request", {}) if isinstance(event.get("source"), dict) else {}
            if not pr:
                continue
            if "pull_request" not in pr:
                continue
            pr_url: str | None = pr.get("html_url")
            pr_state: str = pr.get("state", "")
            merged: bool = pr.get("merged", False) or pr.get("merged_at") is not None
            if pr_state == "closed" and not merged:
                continue  # closed but not merged — doesn't count as addressed
            if merged or pr_state == "merged":
                raise IssueNotActionableError(
                    f"Issue {owner}/{repo}#{number} appears to be addressed by "
                    f"a merged pull request: {pr_url or pr.get('url', 'unknown')}."
                )

    @staticmethod
    def parse_issue_url(url: str) -> tuple[str, str, int]:
        """Extract (owner, repo, issue_number) from a GitHub issue/PR URL.

        Raises ValueError if the URL doesn't look like a GitHub issue URL.
        """
        match = _ISSUE_URL_RE.search(url.strip())
        if not match:
            raise ValueError(
                f"Not a recognizable GitHub issue URL: {url!r}. "
                "Expected: https://github.com/owner/repo/issues/123"
            )
        return match.group("owner"), match.group("repo"), int(match.group("number"))

    @staticmethod
    def issue_labels(raw_issue: dict[str, Any]) -> list[str]:
        """Extract label names from a raw GitHub issue dict."""
        return [label["name"] for label in raw_issue.get("labels", [])]
