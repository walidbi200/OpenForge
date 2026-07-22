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
            PermissionError  – 403 (rate-limited or private repo without token)
            FileNotFoundError – 404 (issue / repo not found)
            RuntimeError     – any other HTTP error
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
        if response.status_code == 404:
            raise FileNotFoundError(
                f"Issue #{number} not found in {owner}/{repo}. "
                "Check that the repo and issue number are correct."
            )
        if not response.is_success:
            raise RuntimeError(
                f"GitHub API returned {response.status_code} for "
                f"{owner}/{repo}#{number}: {response.text[:200]}"
            )

        return response.json()

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
