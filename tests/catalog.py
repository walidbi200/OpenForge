"""Validation catalog for RepositoryAnalyzer.

20 representative entries spanning Python, JavaScript/TypeScript, Go, Rust,
Java, PHP, Ruby, and edge cases (monorepo, ruff.toml, Kotlin/Gradle).

Design:
- ``expected``  – what the *current* analyzer returns. All catalog tests pass
                  when the analyzer behaves correctly.
- ``gaps``      – known limitations where heuristics could be improved in a
                  future iteration. Gaps do NOT cause test failures; they are
                  harvested into the validation report as recommendations.
- ``notes``     – free-form context for the report reader.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

# Fields checked in every catalog entry (order matches report columns).
EXPECTED_FIELDS: tuple[str, ...] = (
    "language",
    "framework",
    "package_manager",
    "test_command",
    "formatter",
    "linter",
    "ci",
)


@dataclass(frozen=True)
class Expected:
    """Expected output from RepositoryAnalyzer.analyze()."""
    language: Optional[str]
    framework: Optional[str]
    package_manager: Optional[str]
    test_command: Optional[str]
    formatter: Optional[str]
    linter: Optional[str]
    ci: Optional[str]


@dataclass(frozen=True)
class Gap:
    """A known heuristic gap — improvement opportunity, not a test failure."""
    field: str
    recommended_value: Optional[str]
    recommendation: str


@dataclass
class CatalogEntry:
    id: str
    description: str
    language_tag: str          # top-level group for the report
    files: dict[str, str]      # repo-relative path → file content
    expected: Expected
    gaps: list[Gap] = field(default_factory=list)
    notes: str = ""


# ---------------------------------------------------------------------------
# Helper: shared file content snippets
# ---------------------------------------------------------------------------

_GHA_CI = "name: CI\non: [push]\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v4\n"
_GITLAB_CI = "image: python:3.11\nstages:\n  - test\ntest:\n  stage: test\n  script: pytest\n"
_CIRCLECI = "version: 2.1\njobs:\n  build:\n    docker:\n      - image: cimg/python:3.11\n"


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

CATALOG: list[CatalogEntry] = [

    # ── Python (4 entries) ──────────────────────────────────────────────────

    CatalogEntry(
        id="python-fastapi-uv-ruff",
        description="FastAPI · uv lockfile · ruff (fmt+lint) · pytest · GitHub Actions",
        language_tag="Python",
        files={
            "pyproject.toml": (
                "[project]\n"
                'name = "myapp"\n'
                'version = "0.1.0"\n'
                'requires-python = ">=3.11"\n'
                'dependencies = ["fastapi>=0.110", "uvicorn>=0.29"]\n'
                "\n"
                "[tool.ruff]\n"
                "line-length = 100\n"
                "\n"
                "[tool.pytest.ini_options]\n"
                'testpaths = ["tests"]\n'
            ),
            "uv.lock": "",
            ".github/workflows/ci.yml": _GHA_CI,
            "tests/__init__.py": "",
            "tests/test_app.py": "def test_health(): assert True\n",
            "src/__init__.py": "",
        },
        expected=Expected(
            language="Python",
            framework="FastAPI",
            package_manager="uv",
            test_command="pytest",
            formatter="ruff",
            linter="ruff",
            ci="GitHub Actions",
        ),
    ),

    CatalogEntry(
        id="python-django-poetry-black-flake8",
        description="Django · poetry · black formatter · flake8 linter · GitLab CI",
        language_tag="Python",
        files={
            "pyproject.toml": (
                "[tool.poetry]\n"
                'name = "djangoapp"\n'
                'version = "0.1.0"\n'
                "\n"
                "[tool.poetry.dependencies]\n"
                'python = "^3.11"\n'
                'django = "^4.2"\n'
                'gunicorn = "^21.0"\n'
                "\n"
                "[tool.black]\n"
                "line-length = 88\n"
                "\n"
                "[tool.flake8]\n"
                "max-line-length = 88\n"
            ),
            "poetry.lock": "",
            ".gitlab-ci.yml": _GITLAB_CI,
        },
        expected=Expected(
            language="Python",
            framework="Django",
            package_manager="poetry",
            test_command=None,   # no pytest.ini, no [tool.pytest.ini_options], no test files
            formatter="black",
            linter="flake8",
            ci="GitLab CI",
        ),
        notes="No pytest config or test_*.py files → test_command correctly undetected.",
    ),

    CatalogEntry(
        id="python-flask-pip-plain",
        description="Flask · pip (requirements.txt only) · no tooling · no CI",
        language_tag="Python",
        files={
            "requirements.txt": "flask==2.3.0\nrequests>=2.31\nclick>=8.0\n",
            "app.py": "from flask import Flask\napp = Flask(__name__)\n",
        },
        expected=Expected(
            language="Python",
            framework="Flask",
            package_manager="pip",
            test_command=None,
            formatter=None,
            linter=None,
            ci=None,
        ),
    ),

    CatalogEntry(
        id="python-pyramid-pipenv-flake8file",
        description="Pyramid · Pipfile.lock (pipenv) · .flake8 config file · no CI",
        language_tag="Python",
        files={
            "requirements.txt": "pyramid>=2.0\nwaitress>=2.1\n",
            "Pipfile.lock": "{}",
            ".flake8": "[flake8]\nmax-line-length = 100\n",
        },
        expected=Expected(
            language="Python",
            framework="Pyramid",
            package_manager="pipenv",
            test_command=None,
            formatter=None,
            linter="flake8",
            ci=None,
        ),
        notes="Verifies .flake8 file (not pyproject) is detected as linter config.",
    ),

    # ── TypeScript / JavaScript (5 entries) ─────────────────────────────────

    CatalogEntry(
        id="typescript-nextjs-pnpm-eslint-prettier",
        description="Next.js · pnpm · ESLint (legacy rc) · Prettier · GitHub Actions",
        language_tag="TypeScript",
        files={
            "package.json": json.dumps({
                "name": "nextjs-app",
                "scripts": {"test": "vitest run", "build": "next build"},
                "dependencies": {"next": "^14.0.0", "react": "^18.0.0"},
                "devDependencies": {
                    "typescript": "^5.3.0",
                    "eslint": "^8.0.0",
                    "prettier": "^3.0.0",
                },
            }, indent=2),
            "tsconfig.json": '{"compilerOptions": {"target": "es2017"}}',
            "pnpm-lock.yaml": "",
            ".eslintrc.json": '{"extends": ["next/core-web-vitals"]}',
            ".prettierrc": '{"semi": false, "singleQuote": true}',
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="TypeScript",
            framework="next",
            package_manager="pnpm",
            test_command="pnpm test",
            formatter="prettier",
            linter="eslint",
            ci="GitHub Actions",
        ),
    ),

    CatalogEntry(
        id="typescript-react-yarn-eslint",
        description="React SPA · TypeScript · yarn · ESLint (.eslintrc.js) · jest",
        language_tag="TypeScript",
        files={
            "package.json": json.dumps({
                "name": "react-app",
                "scripts": {"test": "jest --coverage"},
                "dependencies": {"react": "^18.0.0", "react-dom": "^18.0.0"},
                "devDependencies": {
                    "typescript": "^5.3.0",
                    "jest": "^29.0.0",
                    "@types/react": "^18.0.0",
                },
            }, indent=2),
            "tsconfig.json": "{}",
            "yarn.lock": "",
            ".eslintrc.js": "module.exports = { extends: ['react-app'] };\n",
        },
        expected=Expected(
            language="TypeScript",
            framework="react",
            package_manager="yarn",
            test_command="yarn test",
            formatter=None,
            linter="eslint",
            ci=None,
        ),
    ),

    CatalogEntry(
        id="javascript-express-npm-eslint-flat",
        description="Express.js API · npm · ESLint flat config (v9) · mocha",
        language_tag="JavaScript",
        files={
            "package.json": json.dumps({
                "name": "express-api",
                "scripts": {"test": "mocha --recursive"},
                "dependencies": {"express": "^4.18.0"},
                "devDependencies": {"mocha": "^10.0.0"},
            }, indent=2),
            "package-lock.json": "{}",
            "eslint.config.js": "export default [];\n",
        },
        expected=Expected(
            language="JavaScript",
            framework="express",
            package_manager="npm",
            test_command="npm test",
            formatter=None,
            linter="eslint",
            ci=None,
        ),
        notes="Exercises flat ESLint config detection (eslint.config.js, not .eslintrc).",
    ),

    CatalogEntry(
        id="javascript-bun-prettier",
        description="Plain JS · Bun package manager (bun.lockb) · Prettier",
        language_tag="JavaScript",
        files={
            "package.json": json.dumps({
                "name": "bun-app",
                "scripts": {"test": "bun test"},
                "devDependencies": {"prettier": "^3.0.0"},
            }, indent=2),
            "bun.lockb": "",
            ".prettierrc.json": '{"tabWidth": 2}',
        },
        expected=Expected(
            language="JavaScript",
            framework=None,
            package_manager="bun",
            test_command="bun test",  # scripts.test present + pm=bun → "bun test"
            formatter="prettier",
            linter=None,
            ci=None,
        ),
    ),

    CatalogEntry(
        id="typescript-nestjs-npm-eslint-ci",
        description="NestJS TypeScript API · npm · ESLint · GitHub Actions",
        language_tag="TypeScript",
        files={
            "package.json": json.dumps({
                "name": "nestjs-app",
                "scripts": {"test": "jest"},
                "dependencies": {
                    "@nestjs/core": "^10.0.0",
                    "@nestjs/common": "^10.0.0",
                },
                "devDependencies": {
                    "typescript": "^5.3.0",
                    "@nestjs/testing": "^10.0.0",
                    "jest": "^29.0.0",
                },
            }, indent=2),
            "tsconfig.json": "{}",
            "package-lock.json": "{}",
            ".eslintrc.json": "{}",
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="TypeScript",
            framework="@nestjs/core",
            package_manager="npm",
            test_command="npm test",
            formatter=None,
            linter="eslint",
            ci="GitHub Actions",
        ),
    ),

    # ── Go (2 entries) ──────────────────────────────────────────────────────

    CatalogEntry(
        id="go-gin-modules-ci",
        description="Go · Gin framework · go modules (go.sum) · GitHub Actions",
        language_tag="Go",
        files={
            "go.mod": (
                "module example.com/gin-app\n\n"
                "go 1.22\n\n"
                "require github.com/gin-gonic/gin v1.9.1\n"
            ),
            "go.sum": "",
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="Go",
            framework="gin",
            package_manager="go modules",
            test_command="go test ./...",
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap(
                field="formatter",
                recommended_value="gofmt",
                recommendation=(
                    "gofmt is the universal Go formatter. "
                    "Return 'gofmt' unconditionally when language == 'Go'."
                ),
            ),
        ],
    ),

    CatalogEntry(
        id="go-stdlib-gitlabci",
        description="Go stdlib (no framework) · go modules · GitLab CI",
        language_tag="Go",
        files={
            "go.mod": "module example.com/stdlib-app\n\ngo 1.22\n",
            "go.sum": "",
            ".gitlab-ci.yml": _GITLAB_CI,
        },
        expected=Expected(
            language="Go",
            framework=None,
            package_manager="go modules",
            test_command="go test ./...",
            formatter=None,
            linter=None,
            ci="GitLab CI",
        ),
        gaps=[
            Gap(
                field="formatter",
                recommended_value="gofmt",
                recommendation=(
                    "gofmt is the universal Go formatter. "
                    "Return 'gofmt' unconditionally when language == 'Go'."
                ),
            ),
            Gap(
                field="linter",
                recommended_value="golangci-lint",
                recommendation=(
                    "Detect .golangci.yml / .golangci.yaml → linter='golangci-lint'. "
                    "Currently no Go linter detection exists."
                ),
            ),
        ],
    ),

    # ── Rust (2 entries) ────────────────────────────────────────────────────

    CatalogEntry(
        id="rust-axum-cargo-ci",
        description="Rust · Axum · Cargo.lock (cargo) · GitHub Actions",
        language_tag="Rust",
        files={
            "Cargo.toml": (
                "[package]\n"
                'name = "axum-app"\n'
                'version = "0.1.0"\n'
                'edition = "2021"\n'
                "\n"
                "[dependencies]\n"
                'axum = "0.7"\n'
                'tokio = { version = "1", features = ["full"] }\n'
            ),
            "Cargo.lock": "",
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="Rust",
            framework="axum",
            package_manager="cargo",
            test_command="cargo test",
            formatter="rustfmt",
            linter="clippy",
            ci="GitHub Actions",
        ),
    ),

    CatalogEntry(
        id="rust-actix-web-cargo",
        description="Rust · Actix-web · Cargo.lock · no CI config",
        language_tag="Rust",
        files={
            "Cargo.toml": (
                "[package]\n"
                'name = "actix-app"\n'
                'version = "0.1.0"\n'
                'edition = "2021"\n'
                "\n"
                "[dependencies]\n"
                'actix-web = "4"\n'
            ),
            "Cargo.lock": "",
        },
        expected=Expected(
            language="Rust",
            framework="actix-web",
            package_manager="cargo",
            test_command="cargo test",
            formatter="rustfmt",
            linter="clippy",
            ci=None,
        ),
    ),

    # ── Java (2 entries) ────────────────────────────────────────────────────

    CatalogEntry(
        id="java-spring-maven-ci",
        description="Spring Boot · Maven (pom.xml) · GitHub Actions — language detected; pm/test are gaps",
        language_tag="Java",
        files={
            "pom.xml": (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                "<project>\n"
                "  <modelVersion>4.0.0</modelVersion>\n"
                "  <groupId>com.example</groupId>\n"
                "  <artifactId>demo</artifactId>\n"
                "  <version>0.0.1-SNAPSHOT</version>\n"
                "  <dependencies>\n"
                "    <dependency>\n"
                "      <groupId>org.springframework.boot</groupId>\n"
                "      <artifactId>spring-boot-starter-web</artifactId>\n"
                "    </dependency>\n"
                "  </dependencies>\n"
                "</project>\n"
            ),
            ".github/workflows/ci.yml": _GHA_CI,
            "src/main/java/com/example/DemoApplication.java": (
                "package com.example;\npublic class DemoApplication {}\n"
            ),
        },
        expected=Expected(
            language="Java",
            framework=None,     # no Java framework markers in analyzer
            package_manager=None,  # Maven not in lock-file checks
            test_command=None,  # no Java branch in _detect_test_command
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap("package_manager", "maven", "pom.xml → pm='maven'"),
            Gap("test_command", "mvn test", "Maven project → test_command='mvn test'"),
        ],
        notes=(
            "Java is language-detected only. pm, test_command, formatter, and linter "
            "are all gaps — none of them have Java branches in the current heuristics."
        ),
    ),

    CatalogEntry(
        id="java-spring-gradle-ci",
        description="Spring Boot · Gradle (build.gradle) · GitHub Actions — language detected; pm/test are gaps",
        language_tag="Java",
        files={
            "build.gradle": (
                "plugins {\n"
                "    id 'org.springframework.boot' version '3.2.0'\n"
                "    id 'java'\n"
                "}\n"
                "repositories { mavenCentral() }\n"
                "dependencies {\n"
                "    implementation 'org.springframework.boot:spring-boot-starter-web'\n"
                "    testImplementation 'org.junit.jupiter:junit-jupiter'\n"
                "}\n"
            ),
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="Java",
            framework=None,
            package_manager=None,  # build.gradle not in lock-file checks
            test_command=None,
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap("package_manager", "gradle", "build.gradle → pm='gradle'"),
            Gap(
                "test_command",
                "./gradlew test",
                "Gradle wrapper present → test_command='./gradlew test'; "
                "fallback to 'gradle test' if no gradlew.",
            ),
        ],
    ),

    # ── PHP (1 entry) ────────────────────────────────────────────────────────

    CatalogEntry(
        id="php-laravel-composer-ci",
        description="Laravel · Composer lockfile · GitHub Actions — test_command is a gap",
        language_tag="PHP",
        files={
            "composer.json": json.dumps({
                "name": "laravel/laravel",
                "require": {"laravel/framework": "^11.0", "php": "^8.2"},
                "require-dev": {
                    "phpunit/phpunit": "^10.0",
                    "fakerphp/faker": "^1.23",
                },
            }, indent=2),
            "composer.lock": "{}",
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="PHP",
            framework="Laravel",
            package_manager="composer",
            test_command=None,  # no PHP branch in _detect_test_command
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap(
                "test_command",
                "./vendor/bin/phpunit",
                "Detect 'phpunit/phpunit' in composer require-dev → test_command='./vendor/bin/phpunit'.",
            ),
        ],
    ),

    # ── Ruby (1 entry) ───────────────────────────────────────────────────────

    CatalogEntry(
        id="ruby-rails-bundler-ci",
        description="Ruby on Rails · Bundler (Gemfile.lock) · GitHub Actions — test_command is a gap",
        language_tag="Ruby",
        files={
            "Gemfile": (
                "source 'https://rubygems.org'\n"
                "gem 'rails', '~> 7.1'\n"
                "gem 'rspec-rails', group: :test\n"
                "gem 'pg'\n"
            ),
            "Gemfile.lock": "",
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="Ruby",
            framework="Rails",
            package_manager="bundler",
            test_command=None,  # no Ruby branch in _detect_test_command
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap(
                "test_command",
                "bundle exec rspec",
                "Detect 'rspec-rails' in Gemfile → test_command='bundle exec rspec'; "
                "detect 'minitest' → 'bundle exec rake test'.",
            ),
        ],
    ),

    # ── Edge Cases (3 entries) ───────────────────────────────────────────────

    CatalogEntry(
        id="edge-monorepo-python-js",
        description="Monorepo: Python backend + JS/TS frontend at repo root — first marker wins",
        language_tag="Edge Case",
        files={
            "pyproject.toml": (
                "[project]\n"
                'name = "backend"\n'
                'dependencies = ["fastapi>=0.110"]\n'
                "\n"
                "[tool.pytest.ini_options]\n"
                'testpaths = ["backend/tests"]\n'
            ),
            "uv.lock": "",
            "package.json": json.dumps({
                "name": "frontend",
                "scripts": {"test": "vitest run"},
                "dependencies": {"react": "^18.0.0"},
                "devDependencies": {"typescript": "^5.0.0"},
            }, indent=2),
            "tsconfig.json": "{}",
        },
        expected=Expected(
            language="Python",     # pyproject.toml precedes package.json in _LANGUAGE_MARKERS
            framework="FastAPI",
            package_manager="uv",
            test_command="pytest", # [tool.pytest.ini_options] present
            formatter=None,
            linter=None,
            ci=None,
        ),
        gaps=[
            Gap(
                "language",
                "Python+JavaScript",
                "Detect all language markers present; surface secondary languages "
                "in evidence as 'secondary_languages'. Useful for monorepo awareness.",
            ),
        ],
        notes=(
            "Python marker (pyproject.toml) is scanned before JavaScript (package.json). "
            "The TypeScript frontend side is silently ignored. "
            "Gap: report secondary languages in evidence without breaking primary detection."
        ),
    ),

    CatalogEntry(
        id="edge-python-ruff-toml",
        description="Python project using standalone ruff.toml (not [tool.ruff] in pyproject)",
        language_tag="Edge Case",
        files={
            "pyproject.toml": (
                "[project]\n"
                'name = "ruff-toml-app"\n'
                'dependencies = ["flask>=2.0"]\n'
            ),
            "ruff.toml": "line-length = 100\n",
            "requirements.txt": "flask>=2.0\n",
        },
        expected=Expected(
            language="Python",
            framework="Flask",
            package_manager="pip",
            test_command=None,
            formatter="ruff",
            linter="ruff",
            ci=None,
        ),
        notes=(
            "Exercises the ruff.toml branch in _detect_formatter_linter — "
            "distinct from the [tool.ruff] pyproject path."
        ),
    ),

    CatalogEntry(
        id="edge-kotlin-gradle-kts",
        description="Kotlin · Gradle Kotlin DSL (build.gradle.kts) · GitHub Actions — pm/test are gaps",
        language_tag="Edge Case",
        files={
            "build.gradle.kts": (
                'plugins {\n'
                '    kotlin("jvm") version "1.9.0"\n'
                '}\n'
                "repositories { mavenCentral() }\n"
                "dependencies {\n"
                '    testImplementation(kotlin("test"))\n'
                "}\n"
            ),
            ".github/workflows/ci.yml": _GHA_CI,
        },
        expected=Expected(
            language="Kotlin",
            framework=None,
            package_manager=None,  # build.gradle.kts not in lock-file checks
            test_command=None,     # no Kotlin branch in _detect_test_command
            formatter=None,
            linter=None,
            ci="GitHub Actions",
        ),
        gaps=[
            Gap("package_manager", "gradle", "build.gradle.kts → pm='gradle' (same as Groovy DSL)"),
            Gap(
                "test_command",
                "./gradlew test",
                "Kotlin/Gradle project → test_command='./gradlew test'.",
            ),
        ],
        notes="build.gradle.kts is the Kotlin DSL equivalent of build.gradle. Language=Kotlin detected; rest are gaps.",
    ),
]
