"""RepositoryAnalyzer.

Responsibilities: clone the repo, detect language, package manager, test
framework, formatter/linter, CI, and the directories worth paying attention
to. Everything here is deterministic file-presence and manifest-parsing —
no LLM call is required for any of it, on purpose.

The one optional LLM step (a plain-English conventions summary) lives in
openforge.services.openrouter and degrades to `None` with no error if no
API key is set.

Every detection records *why* it was made in `evidence`, so the output is
auditable instead of a black box the Planner Agent just has to trust.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tomllib
from pathlib import Path

from openforge.models import RepositoryContext
from openforge.utils import parse_github_url, slugify_repo_label

logger = logging.getLogger(__name__)

_NOISE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".pytest_cache", ".ruff_cache", ".mypy_cache",
    "target", "vendor", ".idea", ".vscode", "coverage", ".next", ".nuxt",
    ".tox", "site-packages", ".eggs",
}

# (marker filename, language) — first match found wins for "primary" language,
# but every marker present gets recorded in evidence.
_LANGUAGE_MARKERS: list[tuple[str, str]] = [
    ("pyproject.toml", "Python"),
    ("setup.py", "Python"),
    ("requirements.txt", "Python"),
    ("package.json", "JavaScript"),  # upgraded to TypeScript below if applicable
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("composer.json", "PHP"),
    ("Gemfile", "Ruby"),
    ("pom.xml", "Java"),
    ("build.gradle", "Java"),
    ("build.gradle.kts", "Kotlin"),
]

_PY_FRAMEWORK_MARKERS = ["fastapi", "django", "flask", "starlette", "aiohttp", "pyramid"]
_JS_FRAMEWORK_MARKERS = [
    "next", "nuxt", "react", "vue", "@angular/core", "svelte",
    "express", "@nestjs/core", "fastify", "koa",
]
_RUST_FRAMEWORK_MARKERS = ["axum", "actix-web", "rocket", "tokio", "warp"]
_GO_FRAMEWORK_MARKERS = ["gin-gonic/gin", "labstack/echo", "gorilla/mux", "gofiber/fiber"]
_PHP_FRAMEWORK_MARKERS = ["laravel/framework", "symfony/symfony", "symfony/framework-bundle"]
_RUBY_FRAMEWORK_MARKERS = ["rails"]


class RepositoryAnalyzer:
    """Clones and inspects a repository, producing a RepositoryContext.

    Usage::

        analyzer = RepositoryAnalyzer(workspace_dir)
        path, label = analyzer.resolve(target)   # clone if URL, else use as-is
        context = analyzer.analyze(path, label)
    """

    def __init__(self, workspace_dir: Path):
        self.workspace_dir = workspace_dir

    # ---- acquisition -----------------------------------------------------

    def resolve(self, target: str) -> tuple[Path, str]:
        """Turn a CLI argument into (local_path, repo_label).

        `target` can be a GitHub repo URL, a GitHub issue/PR URL (repo is
        extracted from it), or a path that already exists on disk.
        """
        try:
            owner, repo = parse_github_url(target)
        except ValueError:
            local = Path(target).expanduser().resolve()
            if not local.exists():
                raise FileNotFoundError(
                    f"'{target}' is neither a GitHub URL nor an existing local path."
                )
            return local, local.name

        label = slugify_repo_label(owner, repo)
        dest = self.workspace_dir / label
        if dest.exists():
            logger.info("Using existing clone at %s (delete it to re-clone)", dest)
        else:
            self._clone(owner, repo, dest)
        return dest, f"{owner}/{repo}"

    def _clone(self, owner: str, repo: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        clone_url = f"https://github.com/{owner}/{repo}.git"
        logger.info("Cloning %s -> %s", clone_url, dest)
        result = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(dest)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"git clone failed for {clone_url}:\n{result.stderr}")

    # ---- analysis ----------------------------------------------------------

    def analyze(self, path: Path, repo_label: str) -> RepositoryContext:
        evidence: dict[str, str] = {}

        language, lang_evidence = self._detect_language(path)
        evidence.update(lang_evidence)

        framework = self._detect_framework(path, language, evidence)
        package_manager = self._detect_package_manager(path, language, evidence)
        test_command = self._detect_test_command(path, language, evidence, package_manager)
        formatter, linter = self._detect_formatter_linter(path, language, evidence)
        ci = self._detect_ci(path, evidence)
        important_dirs = self._important_directories(path)

        return RepositoryContext(
            repo=repo_label,
            source_path=str(path),
            language=language,
            framework=framework,
            package_manager=package_manager,
            test_command=test_command,
            formatter=formatter,
            linter=linter,
            ci=ci,
            important_directories=important_dirs,
            evidence=evidence,
        )

    def _detect_language(self, path: Path) -> tuple[str | None, dict[str, str]]:
        evidence: dict[str, str] = {}
        found: list[str] = []
        for marker, lang in _LANGUAGE_MARKERS:
            if (path / marker).exists():
                found.append(lang)
                evidence.setdefault("language_candidates", "")
                evidence["language_candidates"] += f"{marker} -> {lang}; "

        if not found:
            return None, evidence

        primary = found[0]
        evidence["language"] = f"found {_LANGUAGE_MARKERS[[l for _, l in _LANGUAGE_MARKERS].index(primary)][0]}"

        # Upgrade JavaScript -> TypeScript if there's real evidence of it.
        if primary == "JavaScript":
            pkg = self._read_package_json(path)
            has_tsconfig = (path / "tsconfig.json").exists()
            has_ts_dep = pkg and "typescript" in {
                **pkg.get("dependencies", {}),
                **pkg.get("devDependencies", {}),
            }
            if has_tsconfig or has_ts_dep:
                primary = "TypeScript"
                evidence["language"] = "found tsconfig.json / typescript dependency"

        return primary, evidence

    def _detect_framework(
        self, path: Path, language: str | None, evidence: dict[str, str]
    ) -> str | None:
        if language in ("Python",):
            deps = self._python_dependency_names(path)
            for marker in _PY_FRAMEWORK_MARKERS:
                if marker in deps:
                    evidence["framework"] = f"'{marker}' found in Python dependencies"
                    return marker.capitalize() if marker != "fastapi" else "FastAPI"

        if language in ("JavaScript", "TypeScript"):
            pkg = self._read_package_json(path) or {}
            deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
            for marker in _JS_FRAMEWORK_MARKERS:
                if marker in deps:
                    evidence["framework"] = f"'{marker}' found in package.json dependencies"
                    return marker

        if language == "Rust":
            deps = self._toml_dependency_names(path / "Cargo.toml")
            for marker in _RUST_FRAMEWORK_MARKERS:
                if marker in deps:
                    evidence["framework"] = f"'{marker}' found in Cargo.toml"
                    return marker

        if language == "Go" and (path / "go.mod").exists():
            go_mod = (path / "go.mod").read_text(errors="ignore")
            for marker in _GO_FRAMEWORK_MARKERS:
                if marker in go_mod:
                    evidence["framework"] = f"'{marker}' found in go.mod"
                    return marker.split("/")[-1]

        if language == "PHP":
            deps = self._json_dependency_names(path / "composer.json")
            for marker in _PHP_FRAMEWORK_MARKERS:
                if marker in deps:
                    evidence["framework"] = f"'{marker}' found in composer.json"
                    return "Laravel" if "laravel" in marker else "Symfony"

        if language == "Ruby" and (path / "Gemfile").exists():
            gemfile = (path / "Gemfile").read_text(errors="ignore")
            if "rails" in gemfile:
                evidence["framework"] = "'rails' found in Gemfile"
                return "Rails"

        return None

    def _detect_package_manager(
        self, path: Path, language: str | None, evidence: dict[str, str]
    ) -> str | None:
        checks: list[tuple[str, str]] = [
            ("uv.lock", "uv"),
            ("poetry.lock", "poetry"),
            ("Pipfile.lock", "pipenv"),
            ("pnpm-lock.yaml", "pnpm"),
            ("yarn.lock", "yarn"),
            ("bun.lockb", "bun"),
            ("package-lock.json", "npm"),
            ("Cargo.lock", "cargo"),
            ("go.sum", "go modules"),
            ("composer.lock", "composer"),
            ("Gemfile.lock", "bundler"),
        ]
        for filename, manager in checks:
            if (path / filename).exists():
                evidence["package_manager"] = f"found {filename}"
                return manager

        if language == "Python" and (path / "requirements.txt").exists():
            evidence["package_manager"] = "found requirements.txt (no lockfile)"
            return "pip"
        return None

    def _detect_test_command(
        self,
        path: Path,
        language: str | None,
        evidence: dict[str, str],
        package_manager: str | None = None,
    ) -> str | None:
        if language == "Python":
            has_pytest_ini = (path / "pytest.ini").exists()
            has_pyproject_pytest = self._pyproject_has_table(path, "tool.pytest.ini_options")
            has_test_files = self._has_python_test_files(path)
            if has_pytest_ini or has_pyproject_pytest or has_test_files:
                evidence["test_command"] = "found pytest config or test_*.py files"
                return "pytest"
            return None

        if language in ("JavaScript", "TypeScript"):
            pkg = self._read_package_json(path) or {}
            scripts = pkg.get("scripts", {})
            if "test" in scripts:
                runner = {"pnpm": "pnpm test", "yarn": "yarn test", "bun": "bun test"}.get(
                    package_manager, "npm test"
                )
                evidence["test_command"] = (
                    f"package.json scripts.test = '{scripts['test']}' "
                    f"(run via {package_manager or 'npm'})"
                )
                return runner
            return None

        if language == "Rust" and (path / "Cargo.toml").exists():
            evidence["test_command"] = "Cargo.toml present (cargo test is the default runner)"
            return "cargo test"

        if language == "Go" and (path / "go.mod").exists():
            evidence["test_command"] = "go.mod present (go test is the default runner)"
            return "go test ./..."

        return None

    def _detect_formatter_linter(
        self, path: Path, language: str | None, evidence: dict[str, str]
    ) -> tuple[str | None, str | None]:
        formatter, linter = None, None

        if language == "Python":
            if self._pyproject_has_table(path, "tool.ruff") or (path / "ruff.toml").exists():
                formatter, linter = "ruff", "ruff"
                evidence["formatter"] = evidence["linter"] = "found ruff config"
            else:
                if self._pyproject_has_table(path, "tool.black"):
                    formatter = "black"
                    evidence["formatter"] = "found [tool.black] in pyproject.toml"
                if (path / ".flake8").exists() or self._pyproject_has_table(path, "tool.flake8"):
                    linter = "flake8"
                    evidence["linter"] = "found flake8 config"

        elif language in ("JavaScript", "TypeScript"):
            has_eslint = any(
                (path / name).exists()
                for name in (
                    ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
                    "eslint.config.js", "eslint.config.mjs", "eslint.config.ts",  # flat config
                )
            )
            has_prettier = any(
                (path / name).exists()
                for name in (
                    ".prettierrc", ".prettierrc.json", ".prettierrc.js",
                    ".prettierrc.yaml", ".prettierrc.mjs", "prettier.config.js",
                )
            )
            if has_eslint:
                linter = "eslint"
                evidence["linter"] = "found an ESLint config file (legacy or flat format)"
            if has_prettier:
                formatter = "prettier"
                evidence["formatter"] = "found a .prettierrc* file"

        elif language == "Rust":
            formatter, linter = "rustfmt", "clippy"
            evidence["formatter"] = evidence["linter"] = "Rust project default toolchain"

        return formatter, linter

    def _detect_ci(self, path: Path, evidence: dict[str, str]) -> str | None:
        checks = [
            (".github/workflows", "GitHub Actions"),
            (".gitlab-ci.yml", "GitLab CI"),
            (".circleci/config.yml", "CircleCI"),
            ("azure-pipelines.yml", "Azure Pipelines"),
            (".travis.yml", "Travis CI"),
        ]
        for rel, name in checks:
            target = path / rel
            if target.is_dir():
                if any(target.iterdir()):
                    evidence["ci"] = f"found workflow files in {rel}"
                    return name
            elif target.exists():
                evidence["ci"] = f"found {rel}"
                return name
        return None

    def _important_directories(self, path: Path) -> list[str]:
        if not path.is_dir():
            return []
        dirs = sorted(
            p.name
            for p in path.iterdir()
            if p.is_dir() and not p.name.startswith(".") and p.name not in _NOISE_DIRS
        )
        return dirs

    # ---- small parsing helpers ---------------------------------------------

    def _read_package_json(self, path: Path) -> dict | None:
        pkg_path = path / "package.json"
        if not pkg_path.exists():
            return None
        try:
            return json.loads(pkg_path.read_text(errors="ignore"))
        except json.JSONDecodeError:
            logger.warning("Couldn't parse package.json at %s", pkg_path)
            return None

    def _json_dependency_names(self, json_path: Path) -> set[str]:
        if not json_path.exists():
            return set()
        try:
            data = json.loads(json_path.read_text(errors="ignore"))
        except json.JSONDecodeError:
            return set()
        return set(data.get("require", {}))

    def _python_dependency_names(self, path: Path) -> set[str]:
        """Best-effort union of dependency names from pyproject.toml and/or
        requirements.txt — lowercased, no version specifiers."""
        names: set[str] = set()

        pyproject = path / "pyproject.toml"
        if pyproject.exists():
            try:
                data = tomllib.loads(pyproject.read_text(errors="ignore"))
            except tomllib.TOMLDecodeError:
                data = {}
            project_deps = data.get("project", {}).get("dependencies", [])
            poetry_deps = (
                data.get("tool", {}).get("poetry", {}).get("dependencies", {})
            )
            names.update(self._strip_version_specifier(d) for d in project_deps)
            names.update(name.lower() for name in poetry_deps if name.lower() != "python")

        requirements = path / "requirements.txt"
        if requirements.exists():
            for line in requirements.read_text(errors="ignore").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    names.add(self._strip_version_specifier(line))

        return names

    def _toml_dependency_names(self, toml_path: Path) -> set[str]:
        if not toml_path.exists():
            return set()
        try:
            data = tomllib.loads(toml_path.read_text(errors="ignore"))
        except tomllib.TOMLDecodeError:
            return set()
        names: set[str] = set()
        for section in ("dependencies", "dev-dependencies"):
            names.update(data.get(section, {}).keys())
        return names

    @staticmethod
    def _strip_version_specifier(dep: str) -> str:
        return re.split(r"[<>=!~\[; ]", dep.strip(), maxsplit=1)[0].strip().lower()

    def _pyproject_has_table(self, path: Path, dotted_table: str) -> bool:
        pyproject = path / "pyproject.toml"
        if not pyproject.exists():
            return False
        try:
            data = tomllib.loads(pyproject.read_text(errors="ignore"))
        except tomllib.TOMLDecodeError:
            return False
        node = data
        for key in dotted_table.split("."):
            if not isinstance(node, dict) or key not in node:
                return False
            node = node[key]
        return True

    def _has_python_test_files(self, path: Path) -> bool:
        for candidate_dir in ("tests", "test"):
            d = path / candidate_dir
            if d.is_dir() and any(d.rglob("test_*.py")):
                return True
        return any(path.glob("test_*.py"))
