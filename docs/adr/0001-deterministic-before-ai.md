# ADR-0001: Deterministic First Before AI

- **Status**: Accepted
- **Date**: 2026-07-21

## Context

AI agent architectures frequently rely on LLMs for parsing text, detecting file types, classifying issues, or extracting tracebacks. This introduces latency, cost, and non-deterministic failures (hallucinations, schema drift, unparseable output) into tasks that can be solved reliably using standard software engineering techniques (regex, AST inspection, marker file analysis, REST APIs).

## Decision

OpenForge strictly enforces a **Deterministic First** rule:
- If a problem can be solved reliably using code, heuristics, regex, or standard APIs without an LLM, it **must** be solved deterministically.
- LLMs are reserved strictly for tasks requiring semantic reasoning (planning, code synthesis, code review, PR synthesis).

## Consequences

- Faster execution, lower API costs, zero hallucination risk in parsing stages.
- `RepositoryAnalyzer` and `IssueExtractor` are 100% deterministic and testable offline with 0% non-determinism.
- LLM prompt design and agent logic are decoupled from basic data ingestion and parsing.
