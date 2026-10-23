"""Deterministic symbol resolution for PlannerAgent.

When an issue references a function / method / class by name but never
mentions the containing file, this module provides a single deterministic
fallback that searches the local repository for the definition and returns
the file path.

Rules (per ARCHITECTURE.md deterministic mandate):
- No LLM, no embeddings, no semantic search, no vector DB, no ranking model.
- Only exact string / regex matching.
- Only runs when literal basename resolution failed to find the symbol's file.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path


logger = logging.getLogger(__name__)

# Regexes that capture candidate symbols from issue text/code blocks.
# Order matters: most specific first.
_SYMBOL_PATTERNS = [
    # Lines like "def test_foo(bar):"  ->  test_foo
    re.compile(r"^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\("),
    # Lines like "class Foo:" or "class Foo(Base):"  ->  Foo
    re.compile(r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[\(:]"),
    # `` `test_run_from_config` `` in markdown prose
    re.compile(r"`([a-zA-Z_][a-zA-Z0-9_]*)`"),
    # Plain word that looks like a test function (starts with test_)
    re.compile(r"\b(test_[a-zA-Z_][a-zA-Z0-9_]*)\b"),
]

# Regex that matches a Python definition line in a source file.
_DEF_PATTERN = re.compile(r"^(?:async\s+)?def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_CLASS_PATTERN = re.compile(r'^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[\(:]')


def extract_candidate_symbols(body: str, code_blocks: list[str]) -> list[str]:
    """Extract unique, ordered candidate symbols from issue text.

    We search both the free-form body and the extracted code blocks.
    Symbols that are likely module paths (contain '/' or '.') are skipped.
    """
    seen: set[str] = set()
    symbols: list[str] = []
    texts = [body] + code_blocks

    for text in texts:
        for line in text.splitlines():
            for pat in _SYMBOL_PATTERNS:
                for m in pat.finditer(line):
                    sym = m.group(1)
                    if sym in seen:
                        continue
                    # Heuristic: skip things that look like file paths or URLs
                    if "/" in sym or sym.startswith("http"):
                        continue
                    seen.add(sym)
                    symbols.append(sym)

    return symbols


def _find_symbol_in_file(file_path: Path, symbol: str) -> bool:
    """Return True if *file_path* contains a definition of *symbol*."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return False

    # Exact regex match for function or class definition.
    pat = re.compile(
        rf"^(?:async\s+)?def\s+{re.escape(symbol)}\s*\("
        rf"|"
        rf"^class\s+{re.escape(symbol)}\s*[\(:]"
    )
    return any(pat.match(line) for line in content.splitlines())


def resolve_symbol_to_file(
    repo_root: Path,
    symbol: str,
    *,
    search_dirs: tuple[str, ...] = ("src", "tests", "test", "lib"),
    source_suffixes: tuple[str, ...] = (".py", ".pyx", ".js", ".ts", ".rs", ".go"),
) -> Path | None:
    """Deterministically locate *symbol* under *repo_root*.

    Strategy:
    1. Search only inside *search_dirs* if they exist; otherwise fall back to
       the entire repo root (excluding common noise dirs).
    2. Look for ``def <symbol>(`` or ``class <symbol>(``.
    3. Return the first match (sorted by path for determinism).
    4. Log exactly what matched.

    If multiple definitions exist, all candidates are logged and the
    alphabetically-first path is returned deterministically.
    """
    dirs_to_search: list[Path] = []
    for d in search_dirs:
        p = repo_root / d
        if p.is_dir():
            dirs_to_search.append(p)

    if not dirs_to_search:
        dirs_to_search = [repo_root]

    _NOISE = {
        ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
        "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
        "target", "vendor", ".idea", ".vscode", "coverage", ".next", ".nuxt",
        ".tox", "site-packages", ".eggs",
    }

    candidates: list[Path] = []
    for search_dir in dirs_to_search:
        for p in search_dir.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in source_suffixes:
                continue
            if any(part in _NOISE for part in p.parts):
                continue
            if _find_symbol_in_file(p, symbol):
                candidates.append(p)

    if not candidates:
        logger.debug("Symbol lookup: %s -> no matches", symbol)
        return None

    # Deterministic: sort by absolute path string, pick first.
    candidates.sort(key=lambda x: str(x))
    best = candidates[0]
    rel_best = best.relative_to(repo_root).as_posix()

    if len(candidates) == 1:
        logger.info(
            "Symbol lookup:\n\n%s\n\n↓\n\n%s",
            symbol,
            rel_best,
        )
    else:
        all_rels = [c.relative_to(repo_root).as_posix() for c in candidates]
        logger.info(
            "Symbol lookup:\n\n%s\n\n↓ multiple matches: %s\n\nChoosing deterministic (alphabetical first): %s",
            symbol,
            all_rels,
            rel_best,
        )

    return best
