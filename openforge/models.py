"""Shared data models, versioned artifacts, and run manifest.

Every stage in the pipeline talks to the next one through a structured,
versioned artifact wrapped in an Artifact container with cryptographic
checksums and rich provenance metadata.

Models in this file:
- SourceArtifact          Rich reference to an input artifact
- Provenance              Audit trail (run ID, FQCN generator, source artifacts)
- Artifact[T]             Generic versioned container wrapping any payload T
- RunManifest             Run index saved to runs/<run_id>/manifest.json
- RepositoryContext       Output payload of RepositoryAnalyzer (Milestone 2)
- IssueFacts             Output payload of IssueExtractor      (Milestone 3)
- ImplementationPlan     Output contract model for PlannerAgent (Milestone 4)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

from openforge import __version__

T = TypeVar("T", bound=BaseModel)


# ---------------------------------------------------------------------------
# Provenance, Artifact Container & Run Manifest
# ---------------------------------------------------------------------------


class SourceArtifact(BaseModel):
    """Rich reference to an input artifact used by a stage."""

    artifact: str  # e.g., "RepositoryContext"
    file_name: str  # e.g., "repository.json"
    run_id: str | None = None
    checksum: str | None = None


class Provenance(BaseModel):
    """Audit metadata answering: who created me, in which run, from what inputs?"""

    run_id: str | None = None
    generator: str  # FQCN e.g. "openforge.services.issue_extractor.IssueExtractor"
    source_artifacts: list[SourceArtifact] = Field(default_factory=list)


def compute_sha256(payload: BaseModel) -> str:
    """Compute sha256 hex digest over normalized JSON payload."""
    raw_json = json.dumps(payload.model_dump(exclude_none=True), sort_keys=True)
    return "sha256:" + hashlib.sha256(raw_json.encode("utf-8")).hexdigest()


class Artifact(BaseModel, Generic[T]):
    """Generic versioned container model wrapping any payload T."""

    schema_version: int = 1
    tool_version: str = Field(default_factory=lambda: __version__)
    artifact: str  # e.g., "IssueFacts"
    generator: str  # FQCN of generating component
    created_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    data_checksum: str = ""
    provenance: Provenance
    data: T

    def to_json_file_dict(self) -> dict:
        """Dump container to JSON-serializable dict."""
        return self.model_dump(exclude_none=True)

    @classmethod
    def wrap(
        cls,
        data: T,
        generator: str,
        run_id: str | None = None,
        source_artifacts: list[SourceArtifact] | None = None,
        schema_version: int = 1,
    ) -> Artifact[T]:
        """Wrap a Pydantic model into a versioned Artifact container."""
        artifact_name = data.__class__.__name__
        checksum = compute_sha256(data)
        prov = Provenance(
            run_id=run_id,
            generator=generator,
            source_artifacts=source_artifacts or [],
        )
        return cls(
            schema_version=schema_version,
            tool_version=__version__,
            artifact=artifact_name,
            generator=generator,
            data_checksum=checksum,
            provenance=prov,
            data=data,
        )


# Backward compatibility alias
ArtifactEnvelope = Artifact


class RunManifest(BaseModel):
    """Manifest index written to runs/<run_id>/manifest.json."""

    run_id: str
    target: str | None = None
    started_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    updated_at: str = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc).isoformat()
    )
    tool_version: str = Field(default_factory=lambda: __version__)
    artifacts: list[str] = Field(default_factory=list)

    def to_json_file_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Milestone 2: Repository Intelligence Model
# ---------------------------------------------------------------------------


class RepositoryContext(BaseModel):
    """Factual properties detected about a target repository."""

    repo: str  # "owner/name", or a local path if not a GitHub URL
    source_path: str  # absolute path on disk where it was cloned/found
    language: str | None = None
    framework: str | None = None
    package_manager: str | None = None
    test_command: str | None = None
    formatter: str | None = None
    linter: str | None = None
    ci: str | None = None
    important_directories: list[str] = Field(default_factory=list)
    evidence: dict[str, str] = Field(default_factory=dict)
    conventions_summary: str | None = None

    def to_json_file_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Milestone 3: Issue Understanding Models
# ---------------------------------------------------------------------------


class CodeBlock(BaseModel):
    """A fenced code block extracted from the issue body."""

    language: str = ""
    content: str = ""


class IssueFacts(BaseModel):
    """Verifiable facts extracted from a GitHub issue (100% deterministic)."""

    issue_url: str | None = None
    issue_number: int | None = None
    issue_title: str
    labels: list[str] = Field(default_factory=list)
    issue_type: Literal[
        "bug", "feature", "docs", "perf", "refactor", "question", "unknown"
    ] = "unknown"
    body: str = ""
    body_length: int = 0
    section_headers: list[str] = Field(default_factory=list)
    code_blocks: list[CodeBlock] = Field(default_factory=list)
    checklist_items: list[str] = Field(default_factory=list)
    mentioned_files: list[str] = Field(default_factory=list)
    referenced_issues: list[int] = Field(default_factory=list)
    has_traceback: bool = False
    tracebacks: list[str] = Field(default_factory=list)
    has_reproduction_steps: bool = False
    extracted_at: str = ""

    def to_json_file_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ---------------------------------------------------------------------------
# Milestone 4: Planning Contract Model (Interface preview)
# ---------------------------------------------------------------------------


class ImplementationPlan(BaseModel):
    """Contract for PlannerAgent (Milestone 4).

    Reasoning output produced by LLM consuming RepositoryContext + IssueFacts.
    """

    goal: str
    """High-level summary of what this plan accomplishes."""

    files_to_modify: list[str] = Field(default_factory=list)
    """Target file paths relative to repository root."""

    implementation_steps: list[str] = Field(default_factory=list)
    """Step-by-step instruction sequence for CodingAgent."""

    tests_to_run: list[str] = Field(default_factory=list)
    """Commands or test files to execute for verification."""

    risks: list[str] = Field(default_factory=list)
    """Identified risks, breaking changes, or side-effects."""

    requires_human: bool = False
    """True if ambiguity requires human sign-off before code generation."""

    def to_json_file_dict(self) -> dict:
        return self.model_dump(exclude_none=True)
