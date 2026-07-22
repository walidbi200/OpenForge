"""Issue Extractor service.

Fully deterministic extraction of verifiable facts from a GitHub issue.
Zero LLM calls.  Zero network calls.  Zero side effects.

Output: IssueFacts — a Pydantic model that PlannerAgent (Phase 3) will
consume together with RepositoryContext to produce a plan.

All reasoning (suspected files, risks, acceptance criteria, confidence)
is deliberately absent here and belongs in PlannerAgent.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from openforge.models import CodeBlock, IssueFacts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Type classification look-up tables
# ---------------------------------------------------------------------------

_LABEL_TYPE_MAP: dict[str, str] = {
    "bug": "bug",
    "defect": "bug",
    "regression": "bug",
    "error": "bug",
    "crash": "bug",
    "security": "bug",
    "vulnerability": "bug",
    "enhancement": "feature",
    "feature": "feature",
    "feature request": "feature",
    "new feature": "feature",
    "documentation": "docs",
    "docs": "docs",
    "performance": "perf",
    "perf": "perf",
    "optimization": "perf",
    "question": "question",
    "help wanted": "question",
    "refactor": "refactor",
    "cleanup": "refactor",
    "chore": "refactor",
    "technical debt": "refactor",
}

# Priority order: most-specific first.
_DOCS_KW = frozenset(["docs", "documentation", "typo", "typos", "readme", "changelog", "spelling", "grammar"])
_PERF_KW = frozenset(["slow", "performance", "speed", "memory", "leak", "leaks", "optimize", "optimizes",
                       "benchmark", "benchmarks", "throughput", "latency", "bottleneck", "bottlenecks"])
_REFACTOR_KW = frozenset(["refactor", "refactors", "refactoring", "cleanup", "reorganize", "restructure", "rename", "extract"])
_QUESTION_KW = frozenset(["how", "why", "what", "when", "should", "could", "can"])
_BUG_KW = frozenset(["bug", "bugs", "error", "errors", "crash", "crashes", "crashing", "fail", "fails", "failed", "failing",
                      "broken", "broke", "fix", "fixes", "fixing", "wrong", "incorrect", "regression", "regressions",
                      "exception", "exceptions", "traceback", "tracebacks", "panic", "panics", "panicked", "npe", "npes",
                      "null", "nil", "undefined", "unexpected", "invalid"])
_FEATURE_KW = frozenset(["add", "adds", "adding", "implement", "implements", "implementing", "support", "supports", "supporting",
                          "feature", "features", "request", "requests", "enhance", "enhancement", "enhancements", "new",
                          "allow", "allows", "allowing", "enable", "enables", "enabling", "introduce", "introduces",
                          "introducing", "create", "creates", "creating", "proposal", "proposals", "suggest", "suggests",
                          "suggesting", "want", "wants"])

# Conventional commit-style prefixes sometimes appear in issue titles.
_PREFIX_MAP: list[tuple[str, str]] = [
    ("fix:", "bug"), ("bug:", "bug"),
    ("feat:", "feature"), ("feature:", "feature"),
    ("docs:", "docs"), ("doc:", "docs"),
    ("perf:", "perf"),
    ("refactor:", "refactor"), ("chore:", "refactor"),
]

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_FILE_RE = re.compile(
    r"\b(?:(?:[\w.-]+/)+)?[\w][\w.-]*"
    r"\.(?:py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|php|rb|"
    r"md|rst|yml|yaml|json|toml|cfg|ini|sh|env|html|htm|css|scss|sql)\b"
)

_ISSUE_REF_RE = re.compile(r"(?<![&\w])#(\d{1,6})\b")

#: Patterns that indicate a stack trace / traceback is present.
_TRACEBACK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Traceback \(most recent call last\)"),          # Python
    re.compile(r"(?:Exception|Error) in thread"),                # Java
    re.compile(r"at [\w$.<>]+\([\w.]+\.java:\d+\)"),            # Java frame
    re.compile(r"goroutine \d+ \["),                             # Go
    re.compile(r"thread '[^']*' panicked"),                      # Rust
    re.compile(r"SIGSEGV|SIGABRT|Segmentation fault"),           # C/native
    re.compile(r"\bError: .{1,120}\n\s+at "),                   # JavaScript (relaxed spacing)
]

_REPRO_HEADER_RE = re.compile(
    r"^\s*#{1,3}\s*(?:steps?\s+to\s+reproduce|how\s+to\s+reproduce|"
    r"reproduction|repro|reproduc)",
    re.MULTILINE | re.IGNORECASE,
)
_NUMBERED_LIST_RE = re.compile(r"^\s*1\.\s", re.MULTILINE)
_CODE_BLOCK_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_CHECKLIST_RE = re.compile(r"^\s*-\s+\[[ xX]\]\s+(.+)$", re.MULTILINE)
_HEADER_RE = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)


# ---------------------------------------------------------------------------
# IssueExtractor
# ---------------------------------------------------------------------------


class IssueExtractor:
    """Extract verifiable facts from a GitHub issue title, body, and labels.

    Usage::

        extractor = IssueExtractor()
        facts = extractor.extract(
            title=raw_issue["title"],
            body=raw_issue["body"] or "",
            labels=[l["name"] for l in raw_issue.get("labels", [])],
            issue_url="https://github.com/...",
            issue_number=42,
        )
        # facts is an IssueFacts instance — serialise with facts.to_json_file_dict()
    """

    def extract(
        self,
        title: str,
        body: str,
        labels: list[str] | None = None,
        issue_url: str | None = None,
        issue_number: int | None = None,
    ) -> IssueFacts:
        """Return an :class:`IssueFacts` populated entirely by deterministic rules."""
        labels = list(labels or [])
        body = body or ""

        code_blocks = self._extract_code_blocks(body)
        tracebacks = self._extract_tracebacks(body, code_blocks)
        has_traceback = bool(tracebacks) or self._body_has_traceback(body)

        return IssueFacts(
            issue_url=issue_url,
            issue_number=issue_number,
            issue_title=title,
            labels=labels,
            issue_type=self._classify_type(title, labels),
            body=body,
            body_length=len(body),
            section_headers=self._extract_headers(body),
            code_blocks=code_blocks,
            checklist_items=self._extract_checklists(body),
            mentioned_files=self._extract_files(body),
            referenced_issues=self._extract_issue_refs(body),
            has_traceback=has_traceback,
            tracebacks=tracebacks,
            has_reproduction_steps=self._detect_reproduction(body, code_blocks),
            extracted_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    # ── Type classification ───────────────────────────────────────────────

    def _classify_type(self, title: str, labels: list[str]) -> str:
        # 1. Labels are the most reliable signal.
        for label in labels:
            ll = label.lower()
            for keyword, type_ in _LABEL_TYPE_MAP.items():
                if keyword in ll:
                    return type_

        title_lower = title.lower().strip()

        # 2. Conventional commit prefix (e.g. "fix: …", "feat: …").
        for prefix, type_ in _PREFIX_MAP:
            if title_lower.startswith(prefix):
                return type_

        # 3. Title keyword matching — priority order.
        tokens = set(re.findall(r"\b\w+\b", title_lower))
        if tokens & _DOCS_KW:
            return "docs"
        if tokens & _PERF_KW:
            return "perf"
        if tokens & _REFACTOR_KW:
            return "refactor"
        if "?" in title and tokens & _QUESTION_KW:
            return "question"
        if tokens & _BUG_KW:
            return "bug"
        if tokens & _FEATURE_KW:
            return "feature"

        return "unknown"

    # ── Structural extraction ─────────────────────────────────────────────

    def _extract_code_blocks(self, body: str) -> list[CodeBlock]:
        return [
            CodeBlock(language=lang, content=content.strip())
            for lang, content in _CODE_BLOCK_RE.findall(body)
        ]

    def _extract_headers(self, body: str) -> list[str]:
        return [h.strip() for h in _HEADER_RE.findall(body)]

    def _extract_checklists(self, body: str) -> list[str]:
        return [m.strip() for m in _CHECKLIST_RE.findall(body)]

    # ── File and reference extraction ─────────────────────────────────────

    def _extract_files(self, body: str) -> list[str]:
        """Find file-path-like strings using a file-extension allow-list."""
        raw = _FILE_RE.findall(body)
        seen: dict[str, None] = {}
        for match in raw:
            base = match.split("/")[-1]
            if len(base) >= 4:          # skip very short matches like "a.py"
                seen[match] = None
        return list(seen)

    def _extract_issue_refs(self, body: str) -> list[int]:
        return sorted(set(int(m) for m in _ISSUE_REF_RE.findall(body)))

    # ── Traceback / error detection ───────────────────────────────────────

    def _extract_tracebacks(
        self, body: str, code_blocks: list[CodeBlock]
    ) -> list[str]:
        """Return text of code blocks that contain a stack trace pattern.

        Most reporters paste tracebacks inside fenced code blocks, so we
        prioritise those.  If nothing is found there we fall back to
        scanning the raw body text.
        """
        found: list[str] = []

        # Primary: check each fenced code block.
        for block in code_blocks:
            if any(p.search(block.content) for p in _TRACEBACK_PATTERNS):
                found.append(block.content)

        if found:
            return found

        # Secondary: scan raw body text (tracebacks outside fences).
        body_no_code = _CODE_BLOCK_RE.sub("", body)
        for p in _TRACEBACK_PATTERNS:
            m = p.search(body_no_code)
            if m:
                # Extract the surrounding paragraph (up to the next blank line).
                start = max(0, body_no_code.rfind("\n\n", 0, m.start()))
                end = body_no_code.find("\n\n", m.end())
                end = end if end != -1 else len(body_no_code)
                found.append(body_no_code[start:end].strip())
                break   # one body-level traceback is enough

        return found

    def _body_has_traceback(self, body: str) -> bool:
        """True if any traceback pattern appears anywhere in the body."""
        return any(p.search(body) for p in _TRACEBACK_PATTERNS)

    # ── Reproduction signal ───────────────────────────────────────────────

    def _detect_reproduction(
        self, body: str, code_blocks: list[CodeBlock]
    ) -> bool:
        if code_blocks:
            return True
        if _REPRO_HEADER_RE.search(body):
            return True
        if _NUMBERED_LIST_RE.search(body):
            return True
        return False
