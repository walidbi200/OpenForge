"""Issue fixture catalog for IssueExtractor validation.

16 representative GitHub issues spanning:
  Languages : Python, TypeScript, JavaScript, Go, Rust, Java, Ruby, PHP
  Types     : bug, feature, docs, perf, question, unknown
  Clarity   : high / medium / low

Each entry specifies:
  title, body, labels           Input to IssueExtractor.extract()
  expected_issue_type           Expected issue_type field
  expected_mentioned_files      Files that MUST appear in mentioned_files
  expected_has_traceback        Expected has_traceback bool
  expected_has_reproduction     Expected has_reproduction_steps bool
  expected_checklist_min        Lower bound on len(checklist_items)
  clarity                       Qualitative rating: "high" | "medium" | "low"
  notes                         Free-form explanation

Catalog tests assert:
  facts.issue_type == expected_issue_type
  facts.has_traceback == expected_has_traceback
  facts.has_reproduction_steps == expected_has_reproduction
  len(facts.checklist_items) >= expected_checklist_min
  all(f in facts.mentioned_files for f in expected_mentioned_files)
"""

from __future__ import annotations

import textwrap
from dataclasses import dataclass, field


@dataclass(frozen=True)
class IssueFixture:
    id: str
    description: str
    title: str
    body: str
    labels: list[str]
    # ── Expected extractor outputs ────────────────────────────────────────
    expected_issue_type: str
    expected_mentioned_files: list[str]   # must be a subset of actual files
    expected_has_traceback: bool
    expected_has_reproduction: bool
    expected_checklist_min: int           # actual count ≥ this
    # ── Metadata ──────────────────────────────────────────────────────────
    clarity: str                          # "high" | "medium" | "low"
    notes: str = ""

    def __post_init__(self):
        assert self.clarity in ("high", "medium", "low"), (
            f"{self.id}: clarity must be 'high', 'medium', or 'low'"
        )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

ISSUE_CATALOG: list[IssueFixture] = [

    # ── Python (4) ──────────────────────────────────────────────────────────

    IssueFixture(
        id="bug-python-fastapi-422",
        description="FastAPI 422 Unprocessable Entity with full Python traceback",
        title="422 Unprocessable Entity when posting nested JSON body",
        body=textwrap.dedent("""\
            ## Bug Report

            When I POST a nested JSON body to `/api/users/`, the endpoint returns 422
            instead of 201.  The body matches the documented schema exactly.

            ## Steps to reproduce

            ```python
            import httpx

            r = httpx.post(
                "http://localhost:8000/api/users/",
                json={"name": "Alice", "address": {"city": "Berlin", "zip": "10115"}},
            )
            print(r.status_code)  # 422
            ```

            ## Traceback

            ```
            Traceback (most recent call last):
              File "/app/routers/users.py", line 34, in create_user
                user = UserCreate(**data)
              File "pydantic/main.py", line 342, in __init__
                raise ValidationError(errors, model)
            pydantic.error_wrappers.ValidationError: 1 validation error for UserCreate
            address
              value is not a valid dict (type=type_error.dict)
            ```

            ## Environment

            FastAPI 0.110, Python 3.11, Pydantic v1.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["app/routers/users.py"],
        expected_has_traceback=True,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="Python traceback in fenced block + code block repro → both signals fire.",
    ),

    IssueFixture(
        id="feature-django-role-permissions",
        description="Django: add role-based permissions to user model with acceptance criteria",
        title="Add role-based permissions to user model",
        body=textwrap.dedent("""\
            ## Feature Request

            We need to support multiple user roles (admin, editor, viewer) with
            different permission sets.  Currently the `User` model in
            `accounts/models.py` has a boolean `is_admin` flag which is insufficient.

            ## Acceptance Criteria

            - [ ] Add a `Role` model with `name` and `permissions` fields
            - [ ] Link `Role` to `User` via a ForeignKey
            - [ ] Gate `views/dashboard.py` behind the `editor` role
            - [ ] Write migrations under `accounts/migrations/`
            - [ ] Update `tests/test_permissions.py` with role-based test cases
            - [ ] Document the new permission model in `docs/permissions.md`

            ## Notes

            Related to #87 and #103.
        """),
        labels=["enhancement"],
        expected_issue_type="feature",
        expected_mentioned_files=["accounts/models.py"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=5,
        clarity="high",
        notes="6-item checklist; references #87 and #103.",
    ),

    IssueFixture(
        id="bug-python-vague-crash",
        description="Vague 'it stopped working' report — no traceback, no repro",
        title="App crashes after update",
        body=textwrap.dedent("""\
            Hi, after updating to the latest version the app just crashes
            when I start it. I don't see any error. Can you help?

            Using Python 3.10 on Windows.
        """),
        labels=[],
        expected_issue_type="bug",   # title keyword "crashes"
        expected_mentioned_files=[],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=0,
        clarity="low",
        notes="No traceback, no code block, no file refs → minimum extractable facts.",
    ),

    IssueFixture(
        id="docs-python-docstring-missing",
        description="Documentation: missing docstring on public API function",
        title="docs: Missing docstring for create_user() in auth module",
        body=textwrap.dedent("""\
            The `create_user()` function in `openforge/auth.py` (line 42) has no
            docstring.  This makes auto-generated API docs incomplete.

            Please add a Google-style docstring explaining:
            - Parameters
            - Return value
            - Exceptions raised (ValueError if email already in use)
        """),
        labels=["documentation"],
        expected_issue_type="docs",
        expected_mentioned_files=["openforge/auth.py"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=0,
        clarity="medium",
        notes="Label 'documentation' → type=docs. Bullet list is not a numbered repro.",
    ),

    # ── TypeScript / JavaScript (4) ─────────────────────────────────────────

    IssueFixture(
        id="bug-typescript-react-infinite-loop",
        description="React useEffect causes infinite re-render — JS stack trace present",
        title="useEffect causes infinite re-render loop in Dashboard component",
        body=textwrap.dedent("""\
            ## Description

            Opening the Dashboard page causes the browser tab to freeze due to an
            infinite render loop.  The component tree re-renders thousands of times
            per second until the tab crashes.

            ## Error

            ```
            Error: Maximum update depth exceeded.
              at checkForNestedUpdates (react-dom.development.js:25186)
              at scheduleUpdateOnFiber (react-dom.development.js:21661)
              at Object.enqueueSetState (react-dom.development.js:12639)
              at Dashboard.tsx:87
            ```

            ## Code

            ```tsx
            // components/Dashboard.tsx
            useEffect(() => {
              setFilters({ ...filters, page: 1 });  // ← mutates filters on every render
            }, [filters]);
            ```

            The fix is to remove `filters` from the dependency array or use a stable ref.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["components/Dashboard.tsx"],
        expected_has_traceback=True,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="JS 'Error: … at ' pattern in fenced block → traceback. Code block → repro.",
    ),

    IssueFixture(
        id="feature-typescript-dark-mode",
        description="TypeScript/React: add dark mode support with acceptance criteria checklist",
        title="Add dark mode support to the dashboard",
        body=textwrap.dedent("""\
            ## Feature Request

            Users have requested a dark mode toggle.  The current theme is defined in
            `styles/theme.ts` and applied globally via `components/ThemeProvider.tsx`.

            ## Acceptance Criteria

            - [ ] Add `darkMode: boolean` property to the theme object in `styles/theme.ts`
            - [ ] Implement a toggle button in `components/Header.tsx`
            - [ ] Persist preference to `localStorage` (key: `theme`)
            - [ ] All existing Storybook stories pass in dark mode
        """),
        labels=["enhancement"],
        expected_issue_type="feature",
        expected_mentioned_files=["styles/theme.ts"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=4,
        clarity="high",
    ),

    IssueFixture(
        id="bug-javascript-express-headers-sent",
        description="Express.js: 'Cannot set headers after they are sent' runtime error",
        title="Error: Cannot set headers after they are sent to the client",
        body=textwrap.dedent("""\
            ## Bug

            The `/api/orders` endpoint occasionally crashes with this error:

            ```
            Error: Cannot set headers after they are sent to the client
              at ServerResponse.setHeader (_http_outgoing.js:470:11)
              at routes/orders.js:56:13
              at Layer.handle [as handle_request] (express/lib/router/layer.js:95:5)
            ```

            ## Steps to reproduce

            1. Send a POST to `/api/orders` with a large payload.
            2. Observe the server log.
            3. The error appears approximately 1 in 10 requests.

            The double-`res.send()` in `routes/orders.js` around line 55 is the likely cause.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["routes/orders.js"],
        expected_has_traceback=True,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="JS Error:..at pattern in fenced block. Numbered list is also a repro signal.",
    ),

    IssueFixture(
        id="question-nestjs-di",
        description="NestJS: question about circular dependency injection (label-classified)",
        title="How do I inject a custom provider into a module without circular dependency?",
        body=textwrap.dedent("""\
            I'm trying to inject `LoggingService` into `UserModule` but I'm getting a
            circular dependency error at startup.

            My current `src/app.module.ts` has:

            ```typescript
            @Module({
              imports: [UserModule, LoggingModule],
            })
            export class AppModule {}
            ```

            Is there a pattern for breaking the circular dependency in NestJS?
            I've tried `forwardRef()` but it didn't help.
        """),
        labels=["question", "help wanted"],
        expected_issue_type="question",
        expected_mentioned_files=["src/app.module.ts"],
        expected_has_traceback=False,
        expected_has_reproduction=True,   # code block present
        expected_checklist_min=0,
        clarity="medium",
        notes="Label 'question' overrides title keywords. Code block → has_reproduction.",
    ),

    # ── Go (2) ──────────────────────────────────────────────────────────────

    IssueFixture(
        id="perf-go-gin-slow-query",
        description="Go/Gin: response time degrades under load — perf label",
        title="Response time degrades significantly with concurrent requests",
        body=textwrap.dedent("""\
            ## Performance Issue

            Under load (50+ concurrent requests) the `/api/products` endpoint response
            time grows from ~5ms to 800ms.  Profiling shows the bottleneck is in the
            database layer in `internal/handlers/products.go`.

            ## Benchmark

            ```
            BenchmarkProductsEndpoint/concurrency=1     5.2 ms ± 0.1
            BenchmarkProductsEndpoint/concurrency=10   12.4 ms ± 1.3
            BenchmarkProductsEndpoint/concurrency=50  817.3 ms ± 42.0
            ```

            The SQL query in `internal/repository/product_repo.go` line 88 runs
            a full table scan without an index on `created_at`.
        """),
        labels=["performance"],
        expected_issue_type="perf",
        expected_mentioned_files=[
            "internal/handlers/products.go",
            "internal/repository/product_repo.go",
        ],
        expected_has_traceback=False,
        expected_has_reproduction=True,   # code block with benchmark
        expected_checklist_min=0,
        clarity="high",
        notes="Label 'performance' → perf. Benchmark code block → has_reproduction.",
    ),

    IssueFixture(
        id="feature-go-structured-logging",
        description="Go: add structured logging middleware — feature with checklist",
        title="Implement structured logging middleware",
        body=textwrap.dedent("""\
            ## Feature Request

            Replace `log.Printf` calls with structured JSON logging using `slog`
            (Go 1.21+).  Currently log output in `cmd/server/main.go` is not
            machine-parseable.

            ## Acceptance Criteria

            - [ ] Add a `middleware/logging.go` package with a Gin middleware
            - [ ] Log request ID, method, path, status, latency, and user agent as JSON
            - [ ] Pass a logger via context to downstream handlers
            - [ ] Add tests in `middleware/logging_test.go`

            ## Non-goals

            Third-party logging libraries (zerolog, zap) are not wanted — `slog` only.
        """),
        labels=["enhancement"],
        expected_issue_type="feature",
        expected_mentioned_files=["cmd/server/main.go"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=4,
        clarity="high",
    ),

    # ── Rust (1) ────────────────────────────────────────────────────────────

    IssueFixture(
        id="bug-rust-axum-unwrap-panic",
        description="Rust/Axum: panic on unwrap() in route handler — Rust backtrace",
        title="Panic: called `Option::unwrap()` on `None` when user not found",
        body=textwrap.dedent("""\
            ## Bug

            Sending a GET request for a non-existent user ID causes the server to panic
            and crash instead of returning 404.

            ## Traceback

            ```
            thread 'tokio-runtime-worker' panicked at 'called `Option::unwrap()` on a `None` value', src/routes/users.rs:67:38
            note: run with `RUST_BACKTRACE=1` environment variable to display a backtrace
            ```

            ## Reproduction

            ```bash
            curl http://localhost:3000/users/99999
            # Server panics, returns connection reset
            ```

            The fix: replace `.unwrap()` with `ok_or(AppError::NotFound)` in
            `src/routes/users.rs`.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["src/routes/users.rs"],
        expected_has_traceback=True,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="Rust 'thread '...' panicked' in fenced block. Two code blocks.",
    ),

    # ── Java (1) ─────────────────────────────────────────────────────────────

    IssueFixture(
        id="bug-java-spring-npe-query",
        description="Java/Spring: NullPointerException in custom JPQL query",
        title="NullPointerException in UserService.findByEmail() with custom JPQL query",
        body=textwrap.dedent("""\
            ## Bug Report

            Calling `findByEmail()` when the email does not exist in the database throws
            an NPE instead of returning `Optional.empty()`.

            ## Stack Trace

            ```
            Exception in thread "http-nio-8080-exec-1" java.lang.NullPointerException
                at com.example.service.UserService.findByEmail(UserService.java:45)
                at com.example.controller.AuthController.login(AuthController.java:78)
                at sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
            ```

            ## Steps to Reproduce

            1. Start the app with an empty database.
            2. POST to `/auth/login` with an email that does not exist.
            3. Observe the 500 error and the NPE in logs.

            The bug is in `UserService.java` where the result of the JPQL query is
            passed directly to the constructor without null checking.
            See also `UserRepository.java` line 31.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["UserService.java"],
        expected_has_traceback=True,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="Java 'Exception in thread' + 'at com.example' pattern.",
    ),

    # ── Ruby (1) ────────────────────────────────────────────────────────────

    IssueFixture(
        id="feature-ruby-rails-graphql",
        description="Ruby/Rails: add GraphQL endpoint — feature with checklist",
        title="Add GraphQL endpoint for user profile queries",
        body=textwrap.dedent("""\
            ## Feature Request

            We need a GraphQL endpoint `/graphql` for mobile clients.
            The REST API in `app/controllers/api/v1/users_controller.rb` has
            too much over-fetching.

            ## Acceptance Criteria

            - [ ] Add the `graphql-ruby` gem to the Gemfile
            - [ ] Create `app/graphql/types/user_type.rb` with id, name, email, avatar fields
            - [ ] Create `app/graphql/queries/user_query.rb`
            - [ ] Mount the endpoint in `config/routes.rb`
            - [ ] Add RSpec tests in `spec/graphql/queries/user_query_spec.rb`

            ## References

            See the graphql-ruby docs and #201.
        """),
        labels=["enhancement"],
        expected_issue_type="feature",
        expected_mentioned_files=["app/controllers/api/v1/users_controller.rb"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=5,
        clarity="high",
    ),

    # ── PHP (1) ──────────────────────────────────────────────────────────────

    IssueFixture(
        id="bug-php-laravel-queue-silent-fail",
        description="PHP/Laravel: queue job silently fails on large payloads",
        title="Queue job silently fails when processing large payloads",
        body=textwrap.dedent("""\
            ## Bug

            Jobs dispatched to the `processing` queue with payloads >1 MB are silently
            swallowed — no exception is thrown, no failed jobs entry is created.

            ## Steps to Reproduce

            1. Dispatch `ProcessPayload::dispatch($largeArray)` where `$largeArray`
               contains 5000+ elements.
            2. Check `php artisan queue:work --queue=processing` output.
            3. The job does not appear in `failed_jobs` but also never completes.

            The serialisation limit in `config/queue.php` may be too small.
            The job class lives in `app/Jobs/ProcessPayload.php`.
        """),
        labels=["bug"],
        expected_issue_type="bug",
        expected_mentioned_files=["config/queue.php", "app/Jobs/ProcessPayload.php"],
        expected_has_traceback=False,
        expected_has_reproduction=True,   # numbered list
        expected_checklist_min=0,
        clarity="medium",
        notes="No fenced block; numbered list → has_reproduction=True.",
    ),

    # ── Edge / mixed (2) ─────────────────────────────────────────────────────

    IssueFixture(
        id="security-xss-user-profile",
        description="Security: reflected XSS — 'security' label maps to type=bug",
        title="XSS vulnerability in user profile description field",
        body=textwrap.dedent("""\
            ## Security Issue

            The user profile description field in `templates/profile.html` is rendered
            without escaping, allowing reflected XSS.

            ## Steps to Reproduce

            1. Log in as any user.
            2. Edit the profile description and save `<script>alert(1)</script>`.
            3. Load the profile page.
            4. Observe the alert box — the script is executed.

            The template line `{{ user.description }}` should be
            `{{ user.description|escape }}`.

            Related to #55.
        """),
        labels=["bug", "security"],
        expected_issue_type="bug",   # "security" label → bug
        expected_mentioned_files=["templates/profile.html"],
        expected_has_traceback=False,
        expected_has_reproduction=True,
        expected_checklist_min=0,
        clarity="high",
        notes="'security' label maps to 'bug'. Numbered steps → has_reproduction.",
    ),

    IssueFixture(
        id="feature-python-plugin-architecture",
        description="Complex feature: plugin system with checklist and open questions",
        title="Support plugin architecture for custom data processors",
        body=textwrap.dedent("""\
            ## Proposal

            Today all data processors are hard-coded in `core/processors.py`.
            We should support loading external plugins so users can add custom
            processors without modifying the library.

            ## Acceptance Criteria

            - [ ] Define a `Processor` abstract base class in `core/base.py`
            - [ ] Implement a `PluginRegistry` in `core/registry.py` using `importlib`
            - [ ] Support entry_points discovery via `pyproject.toml`
            - [ ] Existing processors in `core/processors.py` become built-in plugins
            - [ ] Update `docs/plugins.md` with usage examples

            ## Open Questions

            - Should the plugin interface be async-first or synchronous?
            - What is the minimum Python version we need to support?
        """),
        labels=["enhancement"],
        expected_issue_type="feature",
        expected_mentioned_files=["core/processors.py"],
        expected_has_traceback=False,
        expected_has_reproduction=False,
        expected_checklist_min=5,
        clarity="medium",
        notes="Open questions section is a fact extracted via headers; no traceback.",
    ),
]
