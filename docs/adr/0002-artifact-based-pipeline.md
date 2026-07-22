# ADR-0002: Artifact-Based Pipeline Execution

- **Status**: Accepted
- **Date**: 2026-07-21

## Context

Complex agentic workflows often pass transient in-memory states or modify the user's workspace directly, making runs difficult to inspect, audit, or replay. When a downstream stage fails, diagnosing the root cause requires re-executing previous steps.

## Decision

Every pipeline stage in OpenForge produces a discrete, serialized artifact saved under `runs/<run_id>/`:
- `repository.json` (`Artifact[RepositoryContext]`)
- `issue_facts.json` (`Artifact[IssueFacts]`)
- `implementation_plan.json` (`Artifact[ImplementationPlan]`)
- `manifest.json` (`RunManifest`)

Generated artifacts are never written into cloned source code directories under `workspace/`.

## Consequences

- Full replayability: any downstream component can resume from pre-generated JSON artifacts using `--run <run_id>`.
- Auditability: every run leaves an immutable file footprint on disk.
- Clean isolation: target git working trees remain unpolluted by meta-tooling files.
