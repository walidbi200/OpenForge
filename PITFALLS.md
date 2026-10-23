# Engineering Pitfalls & Lessons Learned

## JSON Parsing Edge Cases

### Problem
The CodingAgent repeatedly failed to parse LLM responses and threw errors indicating the model was returning prose, even when valid JSON was present in the output.

### Root Cause
**Verified Fact**: The agent codebase (`coder.py`) contained a hard-coded naive string check (`if not raw.strip().startswith("{")`) that instantly intercepted and rejected any response beginning with prose. This preempted the robust `extract_json` helper from ever finding the JSON block.

### Resolution
Modified the LLM prompt to explicitly allow the model to think out loud inside a ```json block, though the hard-coded check still blocked the run in the latest benchmark.

### Prevention
Never implement naive string-prefix checks (e.g., `startswith("{")`) when a robust parser (e.g., regex-based extraction) is already designated to handle unstructured LLM outputs.

---

## Sandbox Dependency Assumptions

### Problem
Framework tests (e.g., FastAPI) instantly crashed with `ModuleNotFoundError` for their core dependencies like `starlette`, even though `pip install -e .` was being run.

### Root Cause
**Verified Fact**: To ensure security, the Docker sandbox runs isolated with `--network none`. This causes any runtime `pip install` commands in the sandbox execution script to silently fail (due to `|| true`). Because the base Docker image was not pre-built with the specific framework dependencies, the tests crashed.

### Resolution
Identified that the base Dockerfile must be rebuilt with all missing dependencies (like `httpx` and `pytest-timeout`) pre-baked. 

### Prevention
When running an air-gapped or network-isolated test sandbox, all required optional dependencies and framework internals must be explicitly baked into the base Docker image (e.g. `Dockerfile.sandbox`) rather than relying on runtime installation.

---

## Prompt Rendering with Source-Code Braces

### Problem
The Planner agent suddenly crashed with `KeyError: '\n  "goal"'` when trying to render the planner prompt.

### Root Cause
**Verified Fact**: The `planner.md` prompt template was updated to include raw JSON examples. Python's `str.format()` interprets single curly braces `{}` as format variable placeholders, causing it to attempt string substitution using the JSON keys as variables.

### Resolution
Escaped the literal curly braces in the markdown prompt by doubling them (`{{` and `}}`).

### Prevention
Always double-escape literal curly braces (`{{`, `}}`) in text files that will be dynamically evaluated by Python's `str.format()`.

---

## Zero-Collected Pytest Runs Incorrectly Assessed

### Problem
Validation pipelines were flagging runs as failed (or sometimes successfully passed) when `pytest` collected 0 tests, causing false benchmark signals.

### Root Cause
**Verified Fact**: `pytest` explicitly returns exit code 5 when no tests are collected. The previous test runner logic treated anything other than exit code 0 as a strict test failure.

### Resolution
Updated the evaluation logic to intercept exit code 5 specifically and classify the result as `INCONCLUSIVE` rather than `Failed` or `Passed`.

### Prevention
Test runners must explicitly handle framework-specific exit codes (like pytest exit code 5) and map them to distinct statuses (e.g., INCONCLUSIVE) rather than relying on binary pass/fail logic.

---

## Nested Git Repository Accidentally Staged

### Problem
The parent repository was accidentally tracking the generated validation output repositories (e.g., cloned repos for scrapy or flask).

### Root Cause
**Verified Fact**: The `validation/` directory was used to house dynamically cloned repositories during benchmark runs, but lacked proper `.gitignore` exclusions at the root level, causing git to treat them as untracked nested repositories or staged artifacts.

### Resolution
Added `validation/*/` to the root `.gitignore` file to prevent git from inspecting dynamically generated benchmark repositories.

### Prevention
Always ensure output directories designed to hold dynamic clones or generated artifacts are explicitly excluded in `.gitignore` before executing automation pipelines.
