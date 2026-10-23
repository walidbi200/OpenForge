"""Unit tests for symbol_resolver deterministic fallback.

These tests cover:
1. extract_candidate_symbols — regex extraction from body + code blocks.
2. resolve_symbol_to_file — exact-match regex search in repo source files.
3. _find_symbol_in_file — low-level definition detection.
4. All policies required by the ticket (unique, duplicate, missing, skip rules).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openforge.services.symbol_resolver import (
    _find_symbol_in_file,
    extract_candidate_symbols,
    resolve_symbol_to_file,
)


# ---------------------------------------------------------------------------
# extract_candidate_symbols
# ---------------------------------------------------------------------------


class TestExtractCandidateSymbols:
    def test_def_extraction(self):
        body = "\n".join([
            "def test_session_transaction_ipv6(app):",
            "    pass",
        ])
        syms = extract_candidate_symbols(body, [])
        assert "test_session_transaction_ipv6" in syms

    def test_class_extraction(self):
        body = "class MyClass(Base):\n    pass"
        syms = extract_candidate_symbols(body, [])
        assert "MyClass" in syms
        assert "Base" not in syms  # Base is inside the class pattern? Let's check carefully.
        # Actually: r"^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[\(:" matches "class MyClass(Base):" -> MyClass. Good.
        # If there's no standalone class line for Base, it's not matched.

    def test_backtick_extraction(self):
        body = "Add one case to `test_run_from_config`"
        syms = extract_candidate_symbols(body, [])
        assert "test_run_from_config" in syms

    def test_test_prefix_word(self):
        body = "The test_foo_bar function does something."
        syms = extract_candidate_symbols(body, [])
        assert "test_foo_bar" in syms

    def test_skips_urls_and_paths(self):
        body = "See https://github.com/org/repo and src/main.py."
        syms = extract_candidate_symbols(body, [])
        # These should NOT show up because they have '/' or start with http.
        assert "https" not in syms
        assert "src" not in syms  # Actually, src/main.py split by regex? src doesn't have slash.
        # Wait, src/main.py has slash so not matched.
        # Let's check if "src" alone could appear if it were "`src`".
        # But here it's src/main.py, no regex should match because contains '/'.
        for s in syms:
            assert "/" not in s
            assert not s.startswith("http")

    def test_uniqueness_preserves_order(self):
        body = "`test_dup` then `test_dup` again."
        syms = extract_candidate_symbols(body, [])
        assert syms == ["test_dup"]

    def test_code_blocks_included(self):
        body = ""
        code = ["def helper_one():", "def helper_two():"]
        syms = extract_candidate_symbols(body, code)
        assert syms == ["helper_one", "helper_two"]


# ---------------------------------------------------------------------------
# _find_symbol_in_file
# ---------------------------------------------------------------------------


class TestFindSymbolInFile:
    def test_finds_function(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("def test_run_from_config(a, b, c):\n    pass\n")
        assert _find_symbol_in_file(f, "test_run_from_config") is True

    def test_finds_async_function(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("async def fetch_data():\n    pass\n")
        assert _find_symbol_in_file(f, "fetch_data") is True

    def test_finds_class(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("class MyClass(Base):\n    pass\n")
        assert _find_symbol_in_file(f, "MyClass") is True

    def test_misses_if_not_definition(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("print(test_run_from_config)\n")
        assert _find_symbol_in_file(f, "test_run_from_config") is False

    def test_misses_different_name(self, tmp_path: Path):
        f = tmp_path / "a.py"
        f.write_text("def other_name():\n    pass\n")
        assert _find_symbol_in_file(f, "test_run_from_config") is False


# ---------------------------------------------------------------------------
# resolve_symbol_to_file
# ---------------------------------------------------------------------------


class TestResolveSymbolToFile:
    def test_unique_symbol(self, tmp_path: Path):
        d = tmp_path / "tests"
        d.mkdir()
        f = d / "test_basic.py"
        f.write_text("def test_run_from_config(a, b, c):\n    pass\n")

        result = resolve_symbol_to_file(tmp_path, "test_run_from_config")
        assert result is not None
        assert result.relative_to(tmp_path).as_posix() == "tests/test_basic.py"

    def test_duplicate_symbols_chooses_alphabetical(self, tmp_path: Path):
        a = tmp_path / "a_tests"
        a.mkdir()
        z = tmp_path / "z_tests"
        z.mkdir()
        (a / "test_a.py").write_text("def test_dup(): pass\n")
        (z / "test_z.py").write_text("def test_dup(): pass\n")

        result = resolve_symbol_to_file(tmp_path, "test_dup")
        assert result is not None
        # Alphabetical by absolute path => a_tests comes before z_tests.
        assert "a_tests" in result.as_posix()

    def test_missing_symbol_returns_none(self, tmp_path: Path):
        result = resolve_symbol_to_file(tmp_path, "does_not_exist")
        assert result is None

    def test_respects_search_dirs(self, tmp_path: Path):
        """If standard search_dirs exist, symbols outside them are NOT found."""
        # Create a standard dir (e.g. 'tests') so the fallback-to-root branch is NOT taken.
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "dummy.py").write_text("def dummy(): pass\n")

        # File in 'other' should be ignored because search_dirs exist and 'other' is excluded.
        other = tmp_path / "other"
        other.mkdir()
        (other / "file.py").write_text("def hidden(): pass\n")

        result = resolve_symbol_to_file(tmp_path, "hidden")
        assert result is None

    def test_fallback_to_repo_root_when_no_search_dirs(self, tmp_path: Path):
        # If no standard dirs exist, should fall back to repo root.
        (tmp_path / "file.py").write_text("def root_level(): pass\n")

        result = resolve_symbol_to_file(tmp_path, "root_level")
        assert result is not None
        assert result.relative_to(tmp_path).as_posix() == "file.py"

    def test_skips_noise_directories(self, tmp_path: Path):
        noise = tmp_path / "__pycache__"
        noise.mkdir()
        (noise / "cached.py").write_text("def noisy(): pass\n")

        result = resolve_symbol_to_file(tmp_path, "noisy")
        assert result is None

    def test_only_searches_specified_suffixes(self, tmp_path: Path):
        f = tmp_path / "notes.md"
        f.write_text("def markdown_func(): pass\n")
        result = resolve_symbol_to_file(tmp_path, "markdown_func")
        assert result is None


# ---------------------------------------------------------------------------
# End-to-end integration-style checks
# ---------------------------------------------------------------------------


class TestSymbolResolverIntegration:
    def test_basenames_over_symbols_no_double_inclusion(self, tmp_path: Path):
        """If a file is already resolved by basename, symbol lookup should NOT
        duplicate it in the snippet list (read_mentioned_files enforces this
        via already_resolved set)."""
        # This is tested via test_planner.py because the integration of
        # symbol_resolver with PlannerAgent._read_mentioned_files lives there.
        pass
