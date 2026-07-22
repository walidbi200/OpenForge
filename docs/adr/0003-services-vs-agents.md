# ADR-0003: Architectural Separation of Services and Agents

- **Status**: Accepted
- **Date**: 2026-07-21

## Context

Blurring the distinction between deterministic utilities and LLM agents leads to fragile codebases where helper functions unexpectedly call remote APIs, or agents become bloated with file parsing logic.

## Decision

OpenForge strictly separates codebase modules into two packages:
- `openforge.services`: 100% deterministic software engineering utilities (`RepositoryAnalyzer`, `IssueExtractor`, `GitHubService`). Zero LLM reasoning, zero prompt dependency.
- `openforge.agents`: LLM-powered reasoning modules (`PlannerAgent`, `CodingAgent`, `ReviewAgent`). Consumes structured Pydantic models from services and outputs typed plans/patches.

If a module no longer performs LLM reasoning (e.g. `IssueExtractor`), it is moved to `services/` and removed from `agents/`.

## Consequences

- Clear boundary between testable deterministic logic and probabilistic LLM calls.
- `services/` modules can be unit tested without API keys or mocks.
- `agents/` modules remain lean and focused exclusively on prompt management and semantic reasoning.
