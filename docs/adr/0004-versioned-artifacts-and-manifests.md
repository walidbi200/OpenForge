# ADR-0004: Versioned Artifacts, Provenance, Checksums, and Manifests

- **Status**: Accepted
- **Date**: 2026-07-21

## Context

As tooling evolves across versions (`0.1`, `0.2`), saved artifact files need schema versioning, tool version tracking, data integrity verification, and explicit provenance linkage so downstream components can verify that upstream inputs have not changed.

## Decision

Every output file in `runs/<run_id>/` is wrapped in a generic container (`Artifact[T]`):
```json
{
  "schema_version": 1,
  "tool_version": "0.1.0",
  "artifact": "IssueFacts",
  "generator": "openforge.services.issue_extractor.IssueExtractor",
  "created_at": "2026-07-21T17:00:00Z",
  "data_checksum": "sha256:...",
  "provenance": {
    "run_id": "2026-07-21T17-00-00",
    "generator": "openforge.services.issue_extractor.IssueExtractor",
    "source_artifacts": [
      {
        "artifact": "RepositoryContext",
        "file_name": "repository.json",
        "run_id": "2026-07-21T17-00-00",
        "checksum": "sha256:..."
      }
    ]
  },
  "data": { ... }
}
```

Additionally, every run directory contains a `manifest.json` indexing all produced artifacts and metadata.

## Consequences

- Full forward compatibility and migration capabilities across CLI releases.
- Checksums guarantee input artifact integrity before running expensive LLM reasoning stages.
- `manifest.json` provides a single entrypoint index for each run directory.
