# ADR-0005: Strongly Typed Models Between Pipeline Stages

- **Status**: Accepted
- **Date**: 2026-07-21

## Context

Passing raw strings, unstructured markdown chunks, or generic untyped dictionaries between pipeline stages creates tight implicit coupling, silent key errors, and unexpected type mismatches when updating components.

## Decision

All inter-stage communication in OpenForge uses strongly typed Pydantic models:
- `RepositoryContext`
- `IssueFacts`
- `ImplementationPlan`
- `RunManifest`

Stage components must never accept or return raw untyped dictionaries.

## Consequences

- Type safety: early validation at serialization and deserialization boundaries.
- Explicit API contracts: every component's input requirements are clear to contributors and IDE auto-complete.
- Schema enforcement: Pydantic automatically validates types, defaults, and JSON serialization.
