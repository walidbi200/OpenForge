"""Catalog validation tests for RepositoryAnalyzer.

Structure
─────────
TestCatalogValidation
    One parametrized test per catalog entry.
    Each test builds a synthetic repo, runs analyze(), compares every
    expected field, then records the result.  Expected values reflect
    *current* analyzer behaviour — tests pass on a correct implementation.

TestEdgeCaseBehaviour
    Targeted assertions for named edge cases (no parametrize), verifying
    specific behaviours that are easy to accidentally break.

TestHeuristicGaps
    xfail tests that document desired-but-unimplemented improvements.
    They fail today (and that's expected) and will turn from xfail→pass
    once the heuristic is added.
"""

from __future__ import annotations

import pytest

from openforge.services.repository_analyzer import RepositoryAnalyzer
from tests.catalog import CATALOG, EXPECTED_FIELDS, CatalogEntry
from tests.conftest import EntryResult, FieldResult, build_fixture_repo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_analyzer(tmp_path):
    return RepositoryAnalyzer(workspace_dir=tmp_path / "ws")


def _run_entry(entry: CatalogEntry, tmp_path) -> tuple[EntryResult, object]:
    """Build fixture, run analyzer, return (EntryResult, context)."""
    repo = build_fixture_repo(tmp_path, entry)
    analyzer = _make_analyzer(tmp_path)
    ctx = analyzer.analyze(repo, entry.id)

    field_results = [
        FieldResult(
            name=f,
            expected=getattr(entry.expected, f),
            actual=getattr(ctx, f),
        )
        for f in EXPECTED_FIELDS
    ]
    return EntryResult(entry=entry, field_results=field_results), ctx


# ---------------------------------------------------------------------------
# 1. Parametrized catalog tests
# ---------------------------------------------------------------------------

class TestCatalogValidation:
    """One test per catalog entry; all are expected to PASS."""

    @pytest.mark.parametrize("entry", CATALOG, ids=[e.id for e in CATALOG])
    def test_entry(self, entry: CatalogEntry, tmp_path, validation_report):
        result, _ctx = _run_entry(entry, tmp_path)
        validation_report.append(result)

        failures = result.failed_fields
        assert not failures, (
            f"Catalog entry '{entry.id}' — {len(failures)} field(s) did not match:\n"
            + "\n".join(
                f"  {fr.name}: expected={fr.expected!r}, got={fr.actual!r}"
                for fr in failures
            )
        )


# ---------------------------------------------------------------------------
# 2. Edge-case behaviour tests
# ---------------------------------------------------------------------------

class TestEdgeCaseBehaviour:
    """Pinpoint tests for behaviours that are easy to accidentally regress."""

    def test_monorepo_python_wins_over_js(self, tmp_path):
        """When pyproject.toml and package.json coexist, Python wins (first marker)."""
        repo = tmp_path / "mono"
        repo.mkdir()
        (repo / "pyproject.toml").write_text(
            "[project]\nname = 'mono'\ndependencies = ['fastapi']\n"
        )
        (repo / "package.json").write_text('{"name": "frontend", "dependencies": {"react": "^18"}}')
        (repo / "tsconfig.json").write_text("{}")

        ctx = _make_analyzer(tmp_path).analyze(repo, "mono")
        assert ctx.language == "Python", (
            "pyproject.toml precedes package.json in _LANGUAGE_MARKERS; Python must win"
        )

    def test_javascript_upgrades_to_typescript_via_tsconfig(self, tmp_path):
        """A package.json + tsconfig.json repo must report language=TypeScript."""
        repo = tmp_path / "ts-via-tsconfig"
        repo.mkdir()
        (repo / "package.json").write_text('{"name": "app"}')
        (repo / "tsconfig.json").write_text("{}")

        ctx = _make_analyzer(tmp_path).analyze(repo, "ts-via-tsconfig")
        assert ctx.language == "TypeScript"

    def test_javascript_upgrades_to_typescript_via_dep(self, tmp_path):
        """package.json with typescript in devDependencies → TypeScript, no tsconfig needed."""
        repo = tmp_path / "ts-via-dep"
        repo.mkdir()
        (repo / "package.json").write_text(
            '{"name": "app", "devDependencies": {"typescript": "^5"}}'
        )
        ctx = _make_analyzer(tmp_path).analyze(repo, "ts-via-dep")
        assert ctx.language == "TypeScript"

    def test_eslint_flat_config_detected(self, tmp_path):
        """eslint.config.js (flat format, ESLint v9) must still set linter='eslint'."""
        repo = tmp_path / "flat-eslint"
        repo.mkdir()
        (repo / "package.json").write_text('{"name": "app"}')
        (repo / "eslint.config.js").write_text("export default [];")

        ctx = _make_analyzer(tmp_path).analyze(repo, "flat-eslint")
        assert ctx.linter == "eslint"

    def test_ruff_toml_sets_formatter_and_linter(self, tmp_path):
        """ruff.toml at repo root (without [tool.ruff] in pyproject) triggers detection."""
        repo = tmp_path / "ruff-toml"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (repo / "ruff.toml").write_text("line-length = 100\n")
        (repo / "requirements.txt").write_text("flask\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "ruff-toml")
        assert ctx.formatter == "ruff"
        assert ctx.linter == "ruff"

    def test_bun_lockb_selects_bun_test_command(self, tmp_path):
        """bun.lockb present → pm=bun, and scripts.test is run as 'bun test'."""
        repo = tmp_path / "bun-app"
        repo.mkdir()
        (repo / "package.json").write_text(
            '{"name": "app", "scripts": {"test": "bun test"}}'
        )
        (repo / "bun.lockb").write_text("")

        ctx = _make_analyzer(tmp_path).analyze(repo, "bun-app")
        assert ctx.package_manager == "bun"
        assert ctx.test_command == "bun test"

    def test_lockfile_priority_uv_beats_pip(self, tmp_path):
        """uv.lock takes priority over requirements.txt for package_manager."""
        repo = tmp_path / "uv-and-reqs"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (repo / "uv.lock").write_text("")
        (repo / "requirements.txt").write_text("flask\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "uv-and-reqs")
        assert ctx.package_manager == "uv", "uv.lock must beat requirements.txt"

    def test_pytest_discovered_via_test_files(self, tmp_path):
        """test_*.py files in a tests/ dir → test_command='pytest' even without config."""
        repo = tmp_path / "pytest-via-files"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_main.py").write_text("def test_ok(): pass\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "pytest-via-files")
        assert ctx.test_command == "pytest"

    def test_pnpm_lock_selects_pnpm_test(self, tmp_path):
        """pnpm-lock.yaml → pm=pnpm → test command prefixed with 'pnpm test'."""
        repo = tmp_path / "pnpm-app"
        repo.mkdir()
        (repo / "package.json").write_text(
            '{"name": "app", "scripts": {"test": "vitest run"}}'
        )
        (repo / "pnpm-lock.yaml").write_text("")

        ctx = _make_analyzer(tmp_path).analyze(repo, "pnpm-app")
        assert ctx.package_manager == "pnpm"
        assert ctx.test_command == "pnpm test"

    def test_empty_directory_does_not_crash(self, tmp_path):
        """An empty directory must return all-None context without raising."""
        repo = tmp_path / "empty"
        repo.mkdir()

        ctx = _make_analyzer(tmp_path).analyze(repo, "empty")
        assert ctx.language is None
        assert ctx.framework is None
        assert ctx.package_manager is None
        assert ctx.test_command is None
        assert ctx.formatter is None
        assert ctx.linter is None
        assert ctx.ci is None

    def test_circleci_detected(self, tmp_path):
        """CircleCI config directory → ci='CircleCI'."""
        repo = tmp_path / "circle"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = 'app'\n")
        (repo / ".circleci").mkdir()
        (repo / ".circleci" / "config.yml").write_text("version: 2.1\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "circle")
        assert ctx.ci == "CircleCI"

    def test_kotlin_language_detected_via_gradle_kts(self, tmp_path):
        """build.gradle.kts → language='Kotlin' (distinct from build.gradle → Java)."""
        repo = tmp_path / "kotlin-app"
        repo.mkdir()
        (repo / "build.gradle.kts").write_text('plugins { kotlin("jvm") version "1.9.0" }\n')

        ctx = _make_analyzer(tmp_path).analyze(repo, "kotlin-app")
        assert ctx.language == "Kotlin"

    def test_php_symfony_detected(self, tmp_path):
        """symfony/framework-bundle in composer.json require → framework='Symfony'."""
        repo = tmp_path / "symfony-app"
        repo.mkdir()
        (repo / "composer.json").write_text(
            '{"require": {"symfony/framework-bundle": "^7.0"}}'
        )
        (repo / "composer.lock").write_text("{}")

        ctx = _make_analyzer(tmp_path).analyze(repo, "symfony-app")
        assert ctx.language == "PHP"
        assert ctx.framework == "Symfony"
        assert ctx.package_manager == "composer"


# ---------------------------------------------------------------------------
# 3. xfail tests documenting desired-but-unimplemented heuristics
# ---------------------------------------------------------------------------

class TestHeuristicGaps:
    """
    These tests are marked xfail — they document improvements that are
    *not yet implemented* but should be. When the heuristic is added,
    remove the xfail mark and the test becomes a regression guard.
    """

    @pytest.mark.xfail(
        reason="Go formatter not returned; gofmt should always be set for Go projects",
        strict=True,
    )
    def test_go_formatter_is_gofmt(self, tmp_path):
        repo = tmp_path / "go-app"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/app\ngo 1.22\n")
        (repo / "go.sum").write_text("")

        ctx = _make_analyzer(tmp_path).analyze(repo, "go-app")
        assert ctx.formatter == "gofmt"

    @pytest.mark.xfail(
        reason="Maven not detected as package_manager; pom.xml → pm='maven' not implemented",
        strict=True,
    )
    def test_java_maven_package_manager(self, tmp_path):
        repo = tmp_path / "java-maven"
        repo.mkdir()
        (repo / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")

        ctx = _make_analyzer(tmp_path).analyze(repo, "java-maven")
        assert ctx.package_manager == "maven"

    @pytest.mark.xfail(
        reason="Java test command not implemented; Maven project should return 'mvn test'",
        strict=True,
    )
    def test_java_maven_test_command(self, tmp_path):
        repo = tmp_path / "java-maven-test"
        repo.mkdir()
        (repo / "pom.xml").write_text("<project><modelVersion>4.0.0</modelVersion></project>")

        ctx = _make_analyzer(tmp_path).analyze(repo, "java-maven-test")
        assert ctx.test_command == "mvn test"

    @pytest.mark.xfail(
        reason="Gradle not detected as package_manager; build.gradle → pm='gradle' not implemented",
        strict=True,
    )
    def test_java_gradle_package_manager(self, tmp_path):
        repo = tmp_path / "java-gradle"
        repo.mkdir()
        (repo / "build.gradle").write_text("plugins { id 'java' }\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "java-gradle")
        assert ctx.package_manager == "gradle"

    @pytest.mark.xfail(
        reason="PHP test command not implemented; phpunit in require-dev should set test_command",
        strict=True,
    )
    def test_php_phpunit_test_command(self, tmp_path):
        import json as _json
        repo = tmp_path / "php-phpunit"
        repo.mkdir()
        (repo / "composer.json").write_text(_json.dumps({
            "require": {"laravel/framework": "^11.0"},
            "require-dev": {"phpunit/phpunit": "^10.0"},
        }))
        (repo / "composer.lock").write_text("{}")

        ctx = _make_analyzer(tmp_path).analyze(repo, "php-phpunit")
        assert ctx.test_command == "./vendor/bin/phpunit"

    @pytest.mark.xfail(
        reason="Ruby test command not implemented; rspec-rails in Gemfile should set test_command",
        strict=True,
    )
    def test_ruby_rspec_test_command(self, tmp_path):
        repo = tmp_path / "ruby-rspec"
        repo.mkdir()
        (repo / "Gemfile").write_text("gem 'rails'\ngem 'rspec-rails'\n")
        (repo / "Gemfile.lock").write_text("")

        ctx = _make_analyzer(tmp_path).analyze(repo, "ruby-rspec")
        assert ctx.test_command == "bundle exec rspec"

    @pytest.mark.xfail(
        reason="Monorepo secondary languages not surfaced; JS side invisible when Python wins",
        strict=True,
    )
    def test_monorepo_secondary_language_in_evidence(self, tmp_path):
        """Secondary language should appear in evidence['secondary_languages']."""
        repo = tmp_path / "mono-sec"
        repo.mkdir()
        (repo / "pyproject.toml").write_text("[project]\nname = 'backend'\n")
        (repo / "package.json").write_text('{"name": "frontend"}')

        ctx = _make_analyzer(tmp_path).analyze(repo, "mono-sec")
        assert "secondary_languages" in ctx.evidence
        assert "JavaScript" in ctx.evidence["secondary_languages"]

    @pytest.mark.xfail(
        reason="Go linter not detected; .golangci.yml → linter='golangci-lint' not implemented",
        strict=True,
    )
    def test_go_golangci_linter(self, tmp_path):
        repo = tmp_path / "go-lint"
        repo.mkdir()
        (repo / "go.mod").write_text("module example.com/app\ngo 1.22\n")
        (repo / "go.sum").write_text("")
        (repo / ".golangci.yml").write_text("run:\n  timeout: 5m\n")

        ctx = _make_analyzer(tmp_path).analyze(repo, "go-lint")
        assert ctx.linter == "golangci-lint"

    @pytest.mark.xfail(
        reason="JS test_command not inferred from deps; no scripts.test but vitest in devDeps",
        strict=True,
    )
    def test_js_test_command_inferred_from_vitest_dep(self, tmp_path):
        """If scripts.test missing but vitest in devDeps, infer 'npx vitest run'."""
        import json as _json
        repo = tmp_path / "vitest-no-script"
        repo.mkdir()
        (repo / "package.json").write_text(_json.dumps({
            "name": "app",
            "devDependencies": {"vitest": "^1.0.0"},
        }))
        (repo / "package-lock.json").write_text("{}")

        ctx = _make_analyzer(tmp_path).analyze(repo, "vitest-no-script")
        assert ctx.test_command is not None
