# Changelog

All notable changes to OpenForge will be documented in this file.

## [1.0.0] - 2026-07-24

### Added
- **Multi-Provider LLM Integration**: Added robust support for both OpenRouter and Dahl Inference APIs.
- **Provider Failover**: Automatically cascade to a secondary provider (e.g. Dahl) if the primary provider hits a 429 quota limit, 401, or 500 server error.
- **SSE Streaming Client**: Enabled `httpx.stream` support to handle extremely large prompt inferences without triggering Cloudflare 524 timeouts.
- **Pluggable Sandboxing**: Introduced `sandbox.py` allowing execution inside `DockerSandbox` or `LocalSandbox`.
- **Static Syntax Repair Loop**: `openforge code` now validates `py_compile` syntax and automatically issues repair chunks for obvious LLM hallucinations before crashing the test suite.
- **Automatic Test Dependency Resolution**: Detects `ImportError` or `ModuleNotFoundError` during the test cycle and dynamically installs missing packages.
- **Structured CLI**: Added `version` and `benchmark` commands, along with full end-to-end `inspect`, `analyze-issue`, `plan`, and `code` commands.
- **Comprehensive E2E Testing**: Established full `scripts/test_determinism.py` guaranteeing identical outputs on 10/10 runs.

### Changed
- **Patch Applicator**: Upgraded CoderAgent to emit `SEARCH/REPLACE` chunk diffs instead of rewriting full files, vastly reducing prompt generation sizes.
- **Pipeline Limit Scaling**: Adjusted context ingestion algorithms to bypass prompt limits (12KB inline file context, 300KB overall context limit).

### Fixed
- Fixed critical branch collision bugs preventing sequential plan generation.
- Fixed 429 quota exhaustion stalling automated workflows.
- Fixed invalid LLM JSON outputs through deterministic multi-pass JSON repair logic.

### Removed
- Removed hardcoded local dependencies and single-provider coupling.
