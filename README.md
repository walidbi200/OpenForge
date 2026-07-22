# OpenForge

AI agent that automates the open-source contribution workflow — issue in,
reviewed pull request out — with a human approval gate before anything is
ever submitted. Working title; rename freely.

## Status: End-to-End Local Execution (MVP)

OpenForge currently implements the first four stages of its pipeline:
1. **Repository Analyzer**: Detects language, framework, and testing tools.
2. **Issue Extractor**: Deterministically extracts facts from a GitHub issue.
3. **Planner Agent**: Drafts a structured implementation plan from the repo and issue facts.
4. **Coding Agent**: Implements the plan on a local branch and runs the test suite.

> [!WARNING]
> **SECURITY WARNING — UNTRUSTED CODE EXECUTION**
> The `openforge code` command checks out untrusted code and automatically executes its test suite on your host machine. Because test runners (like `pytest` or `npm test`) can execute arbitrary code embedded in the repository, you should **only run `openforge code` inside an isolated container, VM, or sandbox.**

## Quick start

```bash
uv sync                        # or: pip install -e ".[dev]"
cp .env.example .env           # fill in OPENROUTER_API_KEY
```

The pipeline is split into four distinct CLI commands that you run in order. Each command reads from and writes to a versioned artifact folder in `runs/<id>/`:

```bash
# 1. Analyze the local repository
uv run openforge inspect ../some-local-clone

# 2. Extract facts from a GitHub issue (requires GITHUB_TOKEN)
uv run openforge analyze-issue https://github.com/owner/repo/issues/123

# 3. Generate an Implementation Plan
uv run openforge plan --run <run-id>

# 4. Generate Code and run tests on a local branch
uv run openforge code --run <run-id>
```

## Run the tests

```bash
uv run pytest -v
```

The test suite runs against synthetic repositories and mocks network calls to ensure the pipeline's deterministic logic works reliably without an active internet connection.

## What's next

Per the phased build-out this scaffold is part of:

1. ~~Repository Analyzer~~ — Done.
2. ~~Issue Extractor~~ — Done.
3. ~~Planner Agent~~ — Done.
4. ~~Coding Agent~~ — Done (writes code + runs tests once).
5. **Testing Agent** (Roadmap) — Parses test failures and feeds them back to the Coding Agent in a capped retry loop.
6. **PR Writer** (Roadmap) — Opens a draft PR. Never auto-published.

Deliberately not in scope yet: issue discovery/ranking, a database instead of JSON files, multi-model orchestration, an automated review agent separate from your own read of the diff.

## Project layout

```text
openforge/
├── openforge/
│   ├── cli.py                    # Typer app entrypoint
│   ├── config.py                 # env loading + logging
│   ├── models.py                 # Core data contracts (RepositoryContext, IssueFacts, ImplementationPlan)
│   ├── utils.py                  # Shared helpers
│   ├── agents/                   # LLM-driven reasoning
│   │   ├── planner.py            # PlannerAgent
│   │   └── coder.py              # CodingAgent
│   └── services/                 # Deterministic execution
│       ├── repository_analyzer.py
│       ├── issue_extractor.py
│       ├── github.py             # GitHub API client
│       └── openrouter.py         # Shared OpenRouter LLM client
├── prompts/                      # LLM prompts versioned as files
├── tests/                        # Comprehensive test suite
├── workspace/                    # Cloned repos
└── runs/                         # Execution artifacts (JSON files)
```
