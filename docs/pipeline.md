# OpenForge Pipeline Architecture

OpenForge is a deterministic-first, artifact-driven automated engineering workflow. It transforms GitHub repository code and issue requests into reliable, high-quality pull requests with explicit human sign-off before anything is submitted upstream.

---

## Complete Pipeline Architecture

```
Repository (Disk / Remote URL)
        │
        ▼
RepositoryAnalyzer (Deterministic)
        │
        ▼
RepositoryAnalysis / RepositoryContext (Artifact)
        │
        ▼
GitHubService (Deterministic REST client)
        │
        ▼
IssueExtractor (Deterministic)
        │
        ▼
IssueFacts (Artifact)
        │
        ▼
PlannerAgent (LLM Reasoning — Milestone 4)
        │
        ▼
ImplementationPlan (Artifact)
        │
        ▼
CodingAgent (LLM Code Generation — Milestone 5)
        │
        ▼
Patch / GitDiff (Artifact)
        │
        ▼
TestingAgent (Deterministic execution + LLM fix loops)
        │
        ▼
ReviewReport (Artifact)
        │
        ▼
ReviewAgent (LLM Verification)
        │
        ▼
Draft Pull Request (GitHub integration with Human Approval)
```

---

## Component Responsibilities

| Component | Nature | Responsibility | Output Artifact |
|---|---|---|---|
| `RepositoryAnalyzer` | Deterministic | Inspects repository marker files to detect language, framework, package manager, test command, formatter, linter, and CI tool. | `repository.json` (`Artifact[RepositoryContext]`) |
| `GitHubService` | Deterministic | Fetches raw issue data via GitHub REST API and parses GitHub issue/PR URLs. | Raw GitHub JSON payload |
| `IssueExtractor` | Deterministic | Extracts verifiable facts (issue type, file mentions, stack traces, code blocks, checklists, references). Zero LLM reasoning. | `issue_facts.json` (`Artifact[IssueFacts]`) |
| `PlannerAgent` | LLM Reasoning | Consumes `RepositoryContext` + `IssueFacts` to formulate a step-by-step implementation plan, target files, and test commands. | `implementation_plan.json` (`Artifact[ImplementationPlan]`) |
| `CodingAgent` | LLM Reasoning | Generates code edits and git patches according to the `ImplementationPlan`. | `patch.diff` (`Artifact[Patch]`) |
| `TestingAgent` | Hybrid | Executes test commands locally; uses LLM only to fix failing tests or compile errors. | `test_results.json` (`Artifact[TestResults]`) |
| `ReviewAgent` | LLM Reasoning | Audits generated patch against repository conventions, safety guidelines, and issue requirements. | `review_report.json` (`Artifact[ReviewReport]`) |
| `PRWriterAgent` | LLM + Human Gate | Drafts PR title and description. Submits pull request only after explicit human approval. | Draft GitHub Pull Request |

---

## Core Architectural Guarantees

### 1. Deterministic First
No LLM is ever used for operations that can be solved reliably with heuristics or regex (e.g. language detection, stack trace extraction, URL parsing).

### 2. AI Only For Reasoning
LLMs are reserved strictly for semantic tasks (planning, code generation, code review, PR synthesis).

### 3. Structured Inputs & Outputs via Versioned Artifact Envelopes
Every pipeline stage consumes typed Pydantic models and produces a versioned artifact wrapped in a generic container:

```json
{
  "schema_version": 1,
  "artifact_type": "IssueFacts",
  "generator": "IssueExtractor",
  "created_at": "2026-07-21T17:00:00Z",
  "provenance": {
    "run_id": "2026-07-21T17-00-00",
    "generator": "IssueExtractor",
    "source_artifacts": ["repository.json"]
  },
  "data": { ... }
}
```

### 4. Every Stage Leaves Artifacts
All execution outputs are written to `runs/<run_id>/`. Cloned repository workspaces remain clean.

### 5. Provenance & Auditability
Every artifact explicitly records its `run_id`, `generator`, and input `source_artifacts`, enabling replaying, debugging, and auditability.
