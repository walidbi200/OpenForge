"""Small, pure helper functions with no side effects — easy to unit test."""

from __future__ import annotations

import re

_GITHUB_PATTERN = re.compile(
    r"github\.com[:/]+(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?(?:/.*)?$"
)


def parse_github_url(url: str) -> tuple[str, str]:
    """Extract (owner, repo) from a GitHub repo URL OR a GitHub issue/PR URL.

    Works for:
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://github.com/owner/repo/issues/123
      git@github.com:owner/repo.git

    Raises ValueError if the URL doesn't look like a GitHub URL at all —
    callers use that to fall back to treating the input as a local path.
    """
    match = _GITHUB_PATTERN.search(url.strip())
    if not match:
        raise ValueError(f"Not a recognizable GitHub URL: {url!r}")
    return match.group("owner"), match.group("repo")


def slugify_repo_label(owner: str, repo: str) -> str:
    """Filesystem-safe directory name for a cloned repo, e.g. 'psf-requests'."""
    return f"{owner}-{repo}".lower()


def extract_json(text: str) -> str:
    """Extract the first JSON object from a string that may contain prose.

    The model is instructed to return only JSON, but may occasionally wrap it
    in a markdown code fence. This function handles both cases.
    """
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)

    brace_match = re.search(r"(\{.*\})", text, re.DOTALL)
    if brace_match:
        return brace_match.group(1)

    return text
