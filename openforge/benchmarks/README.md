# OpenForge Benchmark Framework

This directory contains the deterministic accuracy benchmarking framework for `RepositoryAnalyzer`.

The benchmark allows OpenForge to measure how accurate our static heuristics are against a curated catalog of real open-source repositories.

## Philosophy

- **Deterministic:** No LLM calls. No external APIs during analysis.
- **Fast:** Existing workspace clones are reused by default to make the suite run in seconds.
- **Data-Driven:** Measures accuracy down to the individual field level across multiple repositories.

## Usage

```bash
uv run openforge benchmark
```

This will run the analyzer against the repositories defined in `repositories.json` and output a summary table.

### Flags

- `--repo <name>`: Run the benchmark for a specific repository (e.g. `--repo Flask`). Can be specified multiple times.
- `--update`: Force a clean git clone of the repositories, removing existing ones from the workspace.
- `--json`: Output raw JSON results to the terminal instead of the rich summary.

## Workflow: The Development Gate

Use this benchmark as the primary way to evaluate improvements to `RepositoryAnalyzer`.

```text
Edit heuristic
      ↓
pytest (run unit tests)
      ↓
openforge benchmark
      ↓
Did accuracy improve?
      ↓
Commit
```

This keeps development grounded in measurable improvements instead of anecdotal testing.
