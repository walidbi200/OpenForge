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

    If multiple JSON objects are present (e.g. the model concatenated
    two responses), returns only the first valid one.
    """
    import json

    # 1. Try markdown fenced code block first.
    for fenced in re.finditer(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL):
        candidate = fenced.group(1)
        try:
            json.loads(candidate)
            return candidate
        except (json.JSONDecodeError, ValueError):
            continue

    # 2. Find the first standalone JSON object by bracket matching.
    #    Greedy regex `.*` would span to the *last* `}` and capture
    #    concatenated responses. We walk forward instead.
    start = text.find("{")
    while start != -1:
        # Try every closing brace after start
        end = text.find("}", start + 1)
        while end != -1:
            candidate = text[start:end + 1]
            try:
                json.loads(candidate)
                return candidate
            except (json.JSONDecodeError, ValueError):
                pass
            end = text.find("}", end + 1)
        start = text.find("{", start + 1)

    # Fallback — caller will likely fail json.loads, but at least returned.
    return text


def repair_json(json_str: str) -> str:
    """Attempt to repair common JSON syntax errors and truncation."""
    stack = []
    in_string = False
    escape = False
    
    # 1. Close open structures
    for char in json_str:
        if escape:
            escape = False
            continue
        if char == '"':
            in_string = not in_string
            continue
        if char == '\\' and in_string:
            escape = True
            continue
            
        if not in_string:
            if char in '{[':
                stack.append(char)
            elif char in '}]':
                if stack:
                    stack.pop()

    repaired = json_str
    if in_string:
        repaired += '"'
        
    while stack:
        open_char = stack.pop()
        if open_char == '{':
            repaired += '}'
        elif open_char == '[':
            repaired += ']'
            
    # 2. Fix trailing commas (e.g. `[1, 2, ]` -> `[1, 2]`)
    repaired = re.sub(r',\s*\}', '}', repaired)
    repaired = re.sub(r',\s*\]', ']', repaired)
    
    return repaired
