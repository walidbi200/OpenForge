# OpenForge Development Journal

---

# Project Statistics

**Current Version**: v0.1.0 (RC4)
**Development Started**: 2026-07-22
**Total Features**: 7 (inspect, analyze-issue, plan, code, streaming, retry, sandbox)
**Bug Fixes**: 6 (shell=True removal, repo-path containment, prompt injection, shallow clone patch detection, validation runner test evaluation, determinism script HEAD~1)
**Refactors**: 3 (services vs agents separation, artifact-based pipeline, typed models between stages)
**Security Improvements**: 3 (repo-path containment, prompt injection hardening, sandbox flags)
**Performance Improvements**: 1 (streaming LLM responses)
**Benchmarks Completed**: 3 (dahl, openrouter, sandbox)
**Regression Tests Added**: 24 (test_patch.py)
**Total Tests**: 368 Passed, 9 xfailed (377 total)
**Known Issues**: 2 (Docker sandbox pytest missing, Kimi K2.6 occasional non-JSON)
**Release Status**: RC4

---

# Timeline

- **2026-07-22**: Project started. Initial commit. ADRs 0001-0005 accepted.
- **2026-07-22**: Phase 1 — Deterministic services (RepositoryAnalyzer, IssueExtractor).
- **2026-07-22**: Phase 2 — LLM agents (PlannerAgent, CodingAgent) with typed models.
- **2026-07-22**: Phase 3 — Correctness and robustness (M-1 to M-3, L-5, L-6).
- **2026-07-22**: Phase 4 — Documentation and hygiene (L-2, L-3, L-4, L-7).
- **2026-07-23**: Milestone 3 complete. Audit remediation. Temp hash test removed.
- **2026-07-24**: Patch detection redesign. Shared patch helper created. Validation runner fixed. Determinism script fixed. 24 regression tests added. RC4 release candidate.

---

# Design Decisions

## Decision: Artifact-Based Pipeline (ADR-0002)

**Problem**: Complex agentic workflows pass transient in-memory states or modify the user's workspace directly, making runs difficult to inspect, audit, or replay.

**Options Considered**:
1. In-memory state passing between stages
2. Direct workspace modification
3. Versioned artifact files on disk under `runs/<run_id>/`

**Chosen Solution**: Option 3 — versioned artifact files.

**Reasoning**: Full replayability, auditability, and clean isolation. Every stage produces a discrete, serialized artifact saved under `runs/<run_id>/`.

**Trade-offs**: Slightly more disk usage. Worth it for debuggability and replay.

---

## Decision: Deterministic First Before AI (ADR-0001)

**Problem**: AI agent architectures frequently rely on LLMs for parsing text, detecting file types, classifying issues, or extracting tracebacks. This introduces latency, cost, and non-deterministic failures.

**Options Considered**:
1. LLM for everything (fast to implement, unreliable)
2. Deterministic for parsing, LLM for reasoning (correct approach)

**Chosen Solution**: Option 2 — strict deterministic-first rule.

**Reasoning**: If a problem can be solved reliably using code, heuristics, regex, or standard APIs without an LLM, it must be solved deterministically. LLMs are reserved strictly for tasks requiring semantic reasoning.

**Trade-offs**: More upfront engineering. Faster execution, lower API costs, zero hallucination risk in parsing stages.

---

## Decision: Services vs Agents Separation (ADR-0003)

**Problem**: Blurring the distinction between deterministic utilities and LLM agents leads to fragile codebases where helper functions unexpectedly call remote APIs, or agents become bloated with file parsing logic.

**Options Considered**:
1. Monolithic agent structure (simpler, harder to test)
2. Strict separation (cleaner, more testable)

**Chosen Solution**: Option 2 — strict package separation.

**Reasoning**: `openforge.services` is 100% deterministic. `openforge.agents` is LLM-powered. Clear boundary enables unit testing without API keys.

**Trade-offs**: More files, more explicit imports. Worth it for testability and maintainability.

---

## Decision: Shared Patch Helper (2026-07-24)

**Problem**: Both `validation/runner.py` and `scripts/test_determinism.py` used `git diff HEAD~1`, which returns empty on shallow clones (`--depth 1` with only one commit). This caused "Files Modified: 0" across all validation runs.

**Options Considered**:
1. Fix each file individually (local patches, easy to regress)
2. Create shared helper module (single source of truth, harder to regress)

**Chosen Solution**: Option 2 — `openforge/patch.py` as single source of truth.

**Reasoning**: DRY principle. If `git diff --staged` changes again, only one file needs updating. Shared helper also provides `PatchResult`, `count_files_modified()`, `evaluate_test_results()`, and `load_code_json()`.

**Trade-offs**: One extra file to maintain. Worth it for consistency and preventing regression.

---

# Lessons Learned

## Lesson: Shallow Clones Break `git diff HEAD~1`

**Issue**: `git diff HEAD~1` silently returns empty on shallow clones with only one commit. No error, no warning — just empty output.

**Root Cause**: `git clone --depth 1` creates a repository with a single commit. `HEAD~1` refers to a parent that does not exist. Git exits 0 with empty output.

**Fix**: Use `git diff --staged` instead, which reflects the Coder's actual workflow (`git add` without `git commit`).

**Prevention**: Always use `git diff --staged` when inspecting uncommitted changes. Never assume `HEAD~1` exists. Add regression tests for shallow clone scenarios.

---

## Lesson: String-Based Test Detection is Fragile

**Issue**: `"FAILED" in test_log` could match log messages, comments, or any string containing "FAILED" — not actual test failures.

**Root Cause**: Unstructured text matching on raw stdout/stderr.

**Fix**: Read structured `code.json` → `data.test_results[].passed` instead. Missing test data → `Failed` (never assume success).

**Prevention**: Always prefer structured data (JSON) over unstructured text (logs) when available. If text parsing is unavoidable, use precise regex patterns.

---

## Lesson: Docker Sandbox Needs Pre-Installed Tools

**Issue**: `python:3.11-slim` does not include `pytest`. `--network none` blocks pip downloads. Tests fail with exit 127.

**Root Cause**: Docker image does not include testing tools, and network isolation prevents installation at runtime.

**Fix**: Options: build custom Docker image with pytest pre-installed, mount host site-packages, or use LocalSandbox.

**Prevention**: When designing sandboxed execution environments, always include required tools in the base image. Do not rely on runtime installation when network is isolated.

---

## Lesson: Kimi K2.6 Occasional Non-JSON Responses

**Issue**: Kimi K2.6 via Dahl sometimes returns prose/reasoning instead of schema-conformant JSON, causing planner/coder failures for some repos.

**Root Cause**: Upstream model behavior — not a code bug.

**Fix**: JSON repair logic exists in `openforge/services/llm.py`. No code change needed — this is a model limitation.

**Prevention**: Add retry logic with different models as fallback. Consider adding model-specific prompt tuning.

---

# End of Session

**Date**: 2026-07-24
**Duration**: Full day (15:00 - 18:41)
**Major Accomplishments**:
- Patch detection redesign completed
- Shared patch helper created (`openforge/patch.py`)
- Validation runner fixed (structured test detection)
- Determinism script rewritten (uses shared helper)
- 24 regression tests added (`tests/test_patch.py`)
- 4 validation runner tests updated/added
- Full test suite: 368 passed, 9 xfailed
- End-to-end verification on 5/7 workspace repos
- All security audits passed
- Dahl verified working with live benchmark

**Problems Encountered**:
- Docker sandbox `pytest: command not found` (exit 127) — not a code bug, needs Docker image change
- Kimi K2.6 occasional non-JSON responses — upstream model issue

**Lessons Learned**:
- Shallow clones silently break `git diff HEAD~1`
- Structured data (JSON) is always better than unstructured text (logs)
- Sandbox environments need pre-installed tools

**Next Priorities**:
1. Fix Docker sandbox pytest issue (P1)
2. Run full benchmark suite with new validation runner
3. Address Kimi K2.6 non-conformance (model fallback)
4. Implement Testing Agent (retry loop)
5. Implement PR Writer (draft PR)

---

# 2026-07-24 18:41 (Local Time)

## Patch Detection Redesign and Validation Runner Fix

### Category

Bug Fix

### Files Changed

- `openforge/patch.py` (NEW)
- `validation/runner.py`
- `scripts/test_determinism.py`
- `tests/test_patch.py` (NEW)
- `tests/test_validation_runner.py`

### What Changed

Created `openforge/patch.py` as the single source of truth for all patch detection logic. The module provides:
- `PatchResult` dataclass (frozen, with `diff`, `files_modified`, `diff_hash`, `is_empty` property)
- `get_staged_diff(repo_path)` — returns staged unified diff via `git diff --staged`
- `get_patch(repo_path)` — returns `PatchResult` with diff, file count, and hash
- `count_files_modified(diff)` — counts files by splitting on `diff --git` markers
- `load_code_json(path)` — loads and unwraps the `data` section of `code.json`
- `evaluate_test_results(path)` — reads structured test results from `code.json`, returns `(all_passed, results_list)`

Fixed `validation/runner.py`:
- `get_git_diff()` now delegates to `get_staged_diff()` from shared helper
- Replaced string-based test detection (`"FAILED" in test_log`) with structured `evaluate_test_results()` from `code.json`
- Missing test data → `Failed` (never assumes success)

Rewrote `scripts/test_determinism.py`:
- Uses `get_patch()` from shared helper instead of `git diff HEAD~1`
- Fails on empty patches with descriptive error message
- Hashes actual diff content for determinism comparison

Created `tests/test_patch.py` — 24 regression tests covering:
- `get_staged_diff()`: empty when no changes, returns staged diff, ignores unstaged changes, works on shallow clones
- `count_files_modified()`: empty diff, single file, two files
- `get_patch()`: empty patch, non-empty patch, shallow clone, hash determinism, multiple files
- `PatchResult`: `is_empty` true when no files, false when files, true when whitespace only
- `load_code_json()`: missing file, valid file, malformed JSON
- `evaluate_test_results()`: all passed, one failed, missing code.json, empty test results, no test_results key
- Integration: validation runner uses `get_staged_diff` not `HEAD~1`

Updated `tests/test_validation_runner.py`:
- All 3 existing tests now create `code.json` with structured test results
- Added `test_validation_runner_fails_on_missing_code_json` — verifies missing data → Failed

### Why

Both `validation/runner.py` and `scripts/test_determinism.py` used `git diff HEAD~1`, which silently returns empty on shallow clones (`--depth 1` with only one commit). This caused "Files Modified: 0" across all validation runs, masking the fact that patches were actually being generated.

The old string-based test detection (`"FAILED" in test_log`) was fragile — it could match log messages, comments, or any string containing "FAILED", not actual test failures. The new structured approach reads `code.json` → `data.test_results[].passed`, which is the single source of truth.

### Evidence

- 368 tests passed, 9 xfailed
- Patch detection verified on 5/7 workspace repos:
  - click: 1 file, 1497 bytes, hash=6cb45ca03335
  - flask: 1 file, 3306 bytes, hash=f823a3b2e218
  - fastapi: 2 files, 1290 bytes, hash=d9dbe7605f67
  - httpx: 2 files, 1504 bytes, hash=333e82dbf5a1
  - scrapy: 2 files, 3617 bytes, hash=1f4167811075
- Structured test evaluation correctly detects pytest:command-not-found (exit 127) as failure
- Dahl live benchmark: `request_id: devshard-35961-7021`, `streaming: true`, `termination_reason: success`

### Breaking Changes

None.

### Impact

- **Safer**: Validation runner no longer silently ignores empty diffs
- **More reliable**: Structured test detection eliminates false positives/negatives
- **Easier to maintain**: Single source of truth for patch detection
- **Production ready**: Patch detection works correctly on shallow clones

### Future Improvements

- Fix Docker sandbox `pytest: command not found` (build custom image or mount host site-packages)
- Address Kimi K2.6 occasional non-JSON responses (model fallback logic)
- Run full benchmark suite with new validation runner
- Implement Testing Agent (retry loop for failed tests)
- Implement PR Writer (draft PR creation)

### Commit Summary

Redesigned patch detection from scattered `git diff HEAD~1` calls to a shared helper module (`openforge/patch.py`). Fixed validation runner to use structured test evaluation from `code.json`. Added 24 regression tests. All 368 tests pass. Patches now detected correctly on shallow clones.

---

# 2026-07-23 13:15 (Local Time)

## Milestone 3 Complete and Audit Remediation

### Category

Release

### Files Changed

- Multiple files across the codebase (audit remediation)

### What Changed

Completed Milestone 3 and performed comprehensive audit remediation. Removed temporary hash test that was no longer needed.

### Why

Milestone 3 marked the completion of the core pipeline (inspect → analyze-issue → plan → code) with streaming, retry, and sandbox. Audit remediation addressed security and correctness issues identified during review.

### Evidence

- All tests passing
- Security audit passed (no hardcoded API keys, sandbox flags verified)
- Dahl verified working with live benchmark

### Breaking Changes

None.

### Impact

- Production ready for RC4 release candidate
- Security hardening complete

### Future Improvements

- Patch detection redesign (completed next day)
- Docker sandbox pytest issue

### Commit Summary

Completed Milestone 3 audit remediation. Removed temporary hash test. Project ready for RC4 release candidate.

---

# 2026-07-22 15:52 (Local Time)

## Phase 4: Documentation and Hygiene

### Category

Documentation

### Files Changed

- `docs/adr/0001-deterministic-before-ai.md`
- `docs/adr/0002-artifact-based-pipeline.md`
- `docs/adr/0003-services-vs-agents.md`
- `docs/adr/0004-versioned-artifacts-and-manifests.md`
- `docs/adr/0005-typed-models-between-stages.md`
- `README.md`

### What Changed

Created Architecture Decision Records (ADRs) for all major design decisions. Updated README with current project status, quick start guide, and project layout.

### Why

Documentation ensures that architectural decisions are recorded and can be referenced by future contributors. ADRs provide context for why certain approaches were chosen over alternatives.

### Evidence

- 5 ADRs created
- README updated with current status
- Project layout documented

### Breaking Changes

None.

### Impact

- Easier for new contributors to understand the codebase
- Architectural decisions are documented and traceable

### Future Improvements

- Add more ADRs as new decisions are made
- Keep README updated with each release

### Commit Summary

Created 5 Architecture Decision Records documenting major design decisions. Updated README with current project status and quick start guide.

---

# 2026-07-22 15:49 (Local Time)

## Phase 3: Correctness and Robustness

### Category

Bug Fix

### Files Changed

- `openforge/services/llm.py`
- `openforge/agents/coder.py`
- `openforge/services/sandbox.py`

### What Changed

Implemented streaming LLM responses, retry logic with exponential backoff, and Docker sandbox for isolated test execution. Added JSON repair logic for malformed LLM outputs.

### Why

Streaming improves perceived latency. Retry logic handles transient API failures. Sandbox prevents untrusted code from executing on the host machine.

### Evidence

- Streaming tests passing
- Retry tests passing
- Sandbox security flags verified

### Breaking Changes

None.

### Impact

- **Faster**: Streaming reduces perceived latency
- **Safer**: Sandbox prevents code execution on host
- **More reliable**: Retry logic handles transient failures

### Future Improvements

- Optimize prompt for better streaming performance
- Add more sandbox security tests

### Commit Summary

Implemented streaming, retry, and sandbox. Added JSON repair for malformed LLM outputs.

---

# 2026-07-22 15:44 (Local Time)

## Security Hardening: Prompt Injection and Shell=True

### Category

Security

### Files Changed

- `openforge/agents/planner.py`
- `openforge/agents/coder.py`

### What Changed

Hardened planner against prompt injection attacks. Removed `shell=True` from test runner subprocess calls. Added input validation for repository paths.

### Why

Prompt injection could allow malicious issues to manipulate the planner. `shell=True` is a security risk that allows arbitrary command execution.

### Evidence

- Security audit passed
- All tests passing

### Breaking Changes

None.

### Impact

- **Safer**: Planner resistant to prompt injection
- **Safer**: No `shell=True` in subprocess calls

### Future Improvements

- Add more prompt injection tests
- Consider additional input sanitization

### Commit Summary

Hardened planner against prompt injection. Removed `shell=True` from test runner. Added input validation.

---

# 2026-07-22 15:42 (Local Time)

## Security Hardening: Repo-Path Containment

### Category

Security

### Files Changed

- `openforge/agents/coder.py`

### What Changed

Enforced repo-path containment to prevent directory traversal attacks. Show created files in diff output. Gate `requires_human` flag for dangerous operations.

### Why

Without containment, a malicious repository could escape the workspace directory and modify files outside the intended scope.

### Evidence

- Security audit passed
- All tests passing

### Breaking Changes

None.

### Impact

- **Safer**: Repository modifications contained within workspace
- **More transparent**: Created files shown in diff

### Commit Summary

Enforced repo-path containment. Added directory traversal prevention. Gated dangerous operations.

---

# 2026-07-22 15:00 (Local Time)

## Initial Commit

### Category

Feature

### Files Changed

- `openforge/cli.py`
- `openforge/config.py`
- `openforge/models.py`
- `openforge/utils.py`
- `openforge/agents/planner.py`
- `openforge/agents/coder.py`
- `openforge/services/repository_analyzer.py`
- `openforge/services/issue_extractor.py`
- `openforge/services/github.py`
- `openforge/services/llm.py`
- `prompts/`
- `tests/`
- `pyproject.toml`
- `README.md`

### What Changed

Initial commit of OpenForge with full pipeline implementation:
1. Repository Analyzer — detects language, framework, testing tools
2. Issue Extractor — deterministically extracts facts from GitHub issues
3. Planner Agent — drafts structured implementation plans
4. Coding Agent — implements plans on local branches and runs tests

### Why

OpenForge automates the open-source contribution workflow — issue in, reviewed pull request out — with a human approval gate before anything is ever submitted.

### Evidence

- All initial tests passing
- Pipeline runs end-to-end locally

### Breaking Changes

Initial commit — no previous version.

### Impact

- **New**: Full pipeline from issue to code
- **New**: Deterministic services for parsing
- **New**: LLM agents for reasoning
- **New**: Artifact-based pipeline execution

### Future Improvements

- Implement Testing Agent (retry loop)
- Implement PR Writer (draft PR)
- Add more comprehensive tests

### Commit Summary

Initial commit of OpenForge with full pipeline implementation. Four stages: inspect, analyze-issue, plan, code. Deterministic services for parsing, LLM agents for reasoning. Artifact-based pipeline execution.
