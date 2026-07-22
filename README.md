# OpenForge

AI agent that automates the open-source contribution workflow — issue in,
reviewed pull request out — with a human approval gate before anything is
ever submitted. Working title; rename freely.

## Status: Repository Agent only

This scaffold implements exactly one stage of the full pipeline: **clone a
repo (or point at one on disk) and figure out what it is** — language,
framework, package manager, test command, formatter/linter, CI, and the
directories worth paying attention to. Everything else in the eventual
pipeline (Planner, Coding, Testing, Review, PR Writer agents) is deliberately
not built yet — see "What's next" below for why.

Detection is heuristic (file presence + manifest parsing), not an LLM call.
There's exactly one optional LLM step: a plain-English conventions summary,
which only runs if `OPENROUTER_API_KEY` is set, and the tool works perfectly
well without it.

## Quick start

```bash
uv sync                        # or: pip install -e ".[dev]"
cp .env.example .env           # fill in OPENROUTER_API_KEY if you want the summary step

uv run openforge inspect https://github.com/psf/requests
# or point at an issue URL directly — the repo is extracted from it:
uv run openforge inspect https://github.com/psf/requests/issues/1234
# or a repo you already have on disk:
uv run openforge inspect ../some-local-clone
```

This clones (shallow, `--depth 1`) into `workspace/<owner>-<repo>/`, prints
a report, and writes `repo.json` next to the clone.

## Run the tests

```bash
uv run pytest -v
```

The tests build synthetic repos in `tmp_path` (a FastAPI+ruff+pytest repo, a
plain requirements.txt Flask repo, a TypeScript+React+pnpm+eslint repo, a
Rust+axum repo, a Go+gin repo, and an empty directory) and assert what the
agent should detect for each — no network access required.

## What's next

Per the phased build-out this scaffold is step one of:

1. ~~Repository Agent~~ — this scaffold.
2. **Planner Agent** — takes a GitHub issue + this repo.json, produces a
   structured implementation plan (no code yet).
3. **Coding Agent** — hands the plan to Codex, implements only what the
   plan says, returns a diff.
4. **Testing Agent** — runs `context.test_command`, feeds failures back to
   the Coding Agent, capped retries.
5. **Human approval gate** — you see the diff and test results before
   anything touches GitHub. Never skipped.
6. **PR Writer** — opens a draft PR. Never auto-published.

Deliberately not in scope yet: issue discovery/ranking, a database instead
of JSON files, multi-model orchestration, an automated review agent
separate from your own read of the diff. Add those once steps 1–6 are
proven on real, merged PRs — not before.

## Project layout

```
openforge/
├── openforge/
│   ├── cli.py                    # Typer app — `openforge inspect ...`
│   ├── config.py                 # env loading + logging, one Settings object
│   ├── models.py                 # RepositoryContext (the repo.json shape)
│   ├── utils.py                  # GitHub URL parsing (pure, unit-tested)
│   ├── agents/
│   │   └── repository.py         # clone + heuristic detection
│   └── llm/
│       └── openrouter_client.py  # optional conventions-summary call
├── prompts/
│   └── repo_summary.md           # the one prompt this stage uses, versioned as a file
├── tests/
│   └── test_repository_agent.py
└── workspace/                    # gitignored — cloned repos + repo.json land here
```
