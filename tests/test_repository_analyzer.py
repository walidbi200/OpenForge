import json
from pathlib import Path

import pytest

from openforge.services.repository_analyzer import RepositoryAnalyzer
from openforge.utils import parse_github_url, slugify_repo_label


@pytest.fixture
def analyzer(tmp_path: Path) -> RepositoryAnalyzer:
    return RepositoryAnalyzer(workspace_dir=tmp_path / "workspace")


def make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    return repo


class TestPythonDetection:
    def test_fastapi_project_with_ruff_and_pytest(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "fastapi-style")
        (repo / "pyproject.toml").write_text(
            """
[project]
name = "demo"
dependencies = ["fastapi>=0.110", "uvicorn"]

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
testpaths = ["tests"]
"""
        )
        (repo / "uv.lock").write_text("")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_main.py").write_text("def test_ok(): assert True")
        (repo / ".github" / "workflows").mkdir(parents=True)
        (repo / ".github" / "workflows" / "ci.yml").write_text("name: CI")
        (repo / "app").mkdir()
        (repo / "docs").mkdir()

        ctx = analyzer.analyze(repo, "demo/fastapi-style")

        assert ctx.language == "Python"
        assert ctx.framework == "FastAPI"
        assert ctx.package_manager == "uv"
        assert ctx.test_command == "pytest"
        assert ctx.formatter == "ruff"
        assert ctx.linter == "ruff"
        assert ctx.ci == "GitHub Actions"
        assert "app" in ctx.important_directories
        assert "docs" in ctx.important_directories
        assert ".git" not in ctx.important_directories
        assert "language" in ctx.evidence  # every detection should be explainable

    def test_plain_requirements_txt_project_no_tooling_detected(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "legacy-flask")
        (repo / "requirements.txt").write_text("flask==2.0\nrequests>=2\n")

        ctx = analyzer.analyze(repo, "demo/legacy-flask")

        assert ctx.language == "Python"
        assert ctx.framework == "Flask"
        assert ctx.package_manager == "pip"
        assert ctx.formatter is None
        assert ctx.linter is None
        assert ctx.ci is None


class TestJavaScriptDetection:
    def test_typescript_react_project_with_pnpm_and_eslint(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "ts-app")
        (repo / "package.json").write_text(
            json.dumps(
                {
                    "name": "ts-app",
                    "scripts": {"test": "vitest run"},
                    "dependencies": {"react": "^18.0.0"},
                    "devDependencies": {"typescript": "^5.4.0"},
                }
            )
        )
        (repo / "tsconfig.json").write_text("{}")
        (repo / "pnpm-lock.yaml").write_text("")
        (repo / ".eslintrc.json").write_text("{}")

        ctx = analyzer.analyze(repo, "demo/ts-app")

        assert ctx.language == "TypeScript"
        assert ctx.framework == "react"
        assert ctx.package_manager == "pnpm"
        assert ctx.test_command == "pnpm test"  # matches the detected package manager, not a hardcoded npm
        assert ctx.linter == "eslint"

    def test_flat_config_eslint_is_detected(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "flat-eslint-app")
        (repo / "package.json").write_text(json.dumps({"name": "flat-eslint-app"}))
        (repo / "eslint.config.js").write_text("export default [];")

        ctx = analyzer.analyze(repo, "demo/flat-eslint-app")

        assert ctx.linter == "eslint"

    def test_plain_javascript_without_tsconfig_stays_javascript(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "js-app")
        (repo / "package.json").write_text(json.dumps({"name": "js-app"}))

        ctx = analyzer.analyze(repo, "demo/js-app")

        assert ctx.language == "JavaScript"


class TestRustAndGoDetection:
    def test_rust_axum_project(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "rust-api")
        (repo / "Cargo.toml").write_text(
            """
[package]
name = "rust-api"

[dependencies]
axum = "0.7"
tokio = { version = "1", features = ["full"] }
"""
        )
        (repo / "Cargo.lock").write_text("")

        ctx = analyzer.analyze(repo, "demo/rust-api")

        assert ctx.language == "Rust"
        assert ctx.framework == "axum"
        assert ctx.package_manager == "cargo"
        assert ctx.test_command == "cargo test"
        assert ctx.formatter == "rustfmt"
        assert ctx.linter == "clippy"

    def test_go_project(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "go-svc")
        (repo / "go.mod").write_text("module example.com/go-svc\n\nrequire github.com/gin-gonic/gin v1.9.0")
        (repo / "go.sum").write_text("")

        ctx = analyzer.analyze(repo, "demo/go-svc")

        assert ctx.language == "Go"
        assert ctx.framework == "gin"
        assert ctx.package_manager == "go modules"
        assert ctx.test_command == "go test ./..."


class TestEmptyOrUnknown:
    def test_empty_directory_detects_nothing_without_crashing(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "empty")

        ctx = analyzer.analyze(repo, "demo/empty")

        assert ctx.language is None
        assert ctx.framework is None
        assert ctx.important_directories == []


class TestResolve:
    def test_resolve_local_path_passthrough(self, analyzer, tmp_path):
        repo = make_repo(tmp_path, "local-repo")
        path, label = analyzer.resolve(str(repo))
        assert path == repo.resolve()
        assert label == "local-repo"

    def test_resolve_missing_local_path_raises(self, analyzer, tmp_path):
        with pytest.raises(FileNotFoundError):
            analyzer.resolve(str(tmp_path / "does-not-exist"))


class TestUtils:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://github.com/psf/requests", ("psf", "requests")),
            ("https://github.com/psf/requests.git", ("psf", "requests")),
            ("https://github.com/psf/requests/issues/123", ("psf", "requests")),
            ("git@github.com:psf/requests.git", ("psf", "requests")),
        ],
    )
    def test_parse_github_url(self, url, expected):
        assert parse_github_url(url) == expected

    def test_parse_github_url_rejects_non_github(self):
        with pytest.raises(ValueError):
            parse_github_url("/some/local/path")

    def test_slugify_repo_label(self):
        assert slugify_repo_label("PSF", "Requests") == "psf-requests"
