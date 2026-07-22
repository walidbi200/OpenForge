# OpenForge Architecture Principles & Roadmap

## Core Principles

### 1. Deterministic First

If something can be solved reliably without an LLM, don't use an LLM.

Examples:
- Detect language and framework
- Detect test command, formatter, and linter
- Parse GitHub URLs
- Extract file mentions, stack traces, and checklists from issues

### 2. AI Only For Reasoning

LLMs are reserved strictly for reasoning tasks that cannot be solved deterministically.

Examples:
- Plan creation and tradeoffs
- Code generation and editing
- Code review and verification
- PR description synthesis

### 3. Every Stage Produces a Versioned Artifact

Every stage leaves behind structured, versioned outputs wrapped in an `ArtifactEnvelope[T]` with explicit provenance metadata. All artifacts are saved under `runs/` for inspection and replay.

### 4. Human Approval Before GitHub Writes

OpenForge never pushes branches, comments on issues, or opens pull requests without explicit human sign-off.

### 5. Earn Every Abstraction

Do not introduce databases, vector stores, background task workers, microservices, or complex agent orchestration frameworks. Keep the codebase deterministic, testable, CLI-first, and easy to debug.

---

## Milestone Roadmap

- **Milestone 1 — Foundation**: Workspace isolation, timestamped artifact runs (`runs/`), CLI structure.
- **Milestone 2 — Repository Intelligence**: `RepositoryAnalyzer` heuristics, multi-language support, catalog validation suite.
- **Milestone 3 — Issue Understanding**: `GitHubService` API client, deterministic `IssueExtractor`, `IssueFacts` model.
- **Milestone 4 — Planning**: `PlannerAgent` interface, `ImplementationPlan` model contract.
- **Milestone 5 — Code Generation**: `CodingAgent` patch generation, diff verification, and test-fix loops.
