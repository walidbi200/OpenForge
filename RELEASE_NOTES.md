# OpenForge v1.0.0 Release Notes

We are thrilled to announce the v1.0.0 release of **OpenForge**.

OpenForge is an autonomous AI agent that automates the open-source contribution workflow—from taking an issue URL to delivering a locally tested, ready-to-publish branch.

## What's New in v1.0.0?

This release marks the transition from MVP to a production-ready, deterministic platform.

### High Reliability & Streaming
By migrating the LLM transport layer to an SSE Streaming HTTP client, we've entirely eliminated Cloudflare 524 timeouts on massive 15,000+ token context windows. Generating large multi-file diffs against monolithic repositories is now fluid and reliable.

### Provider Failover Cascade
To eliminate API quota frustration (429s, 401s) and random service disruptions (500s), OpenForge v1.0.0 introduces a failover `RetryManager`. You can configure a primary (`LLM_PROVIDER=openrouter`) and a fallback (`LLM_FALLBACK=dahl`), and OpenForge will seamlessly migrate between providers to guarantee your pipeline finishes successfully.

### Advanced Syntax Validation & Auto-Recovery
OpenForge now executes strict `py_compile` checks before engaging the heavy test runner. If the LLM produces bad indentation or typos, OpenForge instantly intercepts the `SyntaxError` and forces the LLM to patch it in a tight repair loop.

### Test Isolation
We introduced a pluggable `sandbox.py` abstraction. When Docker is detected on the host system, OpenForge will automatically pull an isolated image and run untrusted repository test suites (`pytest`, `npm test`) safely inside a `DockerSandbox`.

## Objective Evidence
Our `v1.0.0` release is backed by 100% passing tests (334 total tests) and deterministic benchmarks. During RC2 validation, our determinism scripts yielded 10 identical successful patches in a row against real-world repositories with 0 hallucinations.

## Getting Started
Clone the repository and run:
```bash
uv sync
cp .env.example .env
uv run openforge --help
```

Thank you to all contributors who stress-tested the platform through RC1 and RC2. Enjoy v1.0.0!
