"""Tests for IssueExtractor and supporting services.

Three test classes:

TestIssueExtractorCatalog
    Parametrized — one test per catalog entry.  Exercises the classifier,
    file-mention regex, traceback detector, and reproduction signal for
    every fixture.  100 % deterministic — no mocks, no network.

TestIssueExtractorEdgeCases
    Targeted unit tests for specific parser behaviours that are easy to
    accidentally regress.

TestGitHubServiceURLParsing
    Deterministic tests for GitHubService.parse_issue_url().
    No network calls.
"""

from __future__ import annotations

import pytest

from openforge.models import CodeBlock, IssueFacts
from openforge.services.github import GitHubService, IssueNotActionableError
from openforge.services.issue_extractor import IssueExtractor
from tests.issue_catalog import ISSUE_CATALOG, IssueFixture


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract(fixture: IssueFixture) -> IssueFacts:
    return IssueExtractor().extract(
        title=fixture.title,
        body=fixture.body,
        labels=fixture.labels,
    )


# ---------------------------------------------------------------------------
# 1. Parametrized catalog tests
# ---------------------------------------------------------------------------


class TestIssueExtractorCatalog:
    """One test per catalog entry; all should pass on a correct implementation."""

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_issue_type(self, fixture: IssueFixture) -> None:
        facts = _extract(fixture)
        assert facts.issue_type == fixture.expected_issue_type, (
            f"[{fixture.id}] Expected issue_type={fixture.expected_issue_type!r}, "
            f"got {facts.issue_type!r}"
        )

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_has_traceback(self, fixture: IssueFixture) -> None:
        facts = _extract(fixture)
        assert facts.has_traceback == fixture.expected_has_traceback, (
            f"[{fixture.id}] Expected has_traceback={fixture.expected_has_traceback}, "
            f"got {facts.has_traceback}"
        )

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_has_reproduction_steps(self, fixture: IssueFixture) -> None:
        facts = _extract(fixture)
        assert facts.has_reproduction_steps == fixture.expected_has_reproduction, (
            f"[{fixture.id}] Expected has_reproduction_steps={fixture.expected_has_reproduction}, "
            f"got {facts.has_reproduction_steps}"
        )

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_checklist_minimum(self, fixture: IssueFixture) -> None:
        facts = _extract(fixture)
        assert len(facts.checklist_items) >= fixture.expected_checklist_min, (
            f"[{fixture.id}] Expected ≥{fixture.expected_checklist_min} checklist items, "
            f"got {len(facts.checklist_items)}"
        )

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_mentioned_files_subset(self, fixture: IssueFixture) -> None:
        """All expected files must appear in the extracted file list."""
        facts = _extract(fixture)
        for expected_file in fixture.expected_mentioned_files:
            assert expected_file in facts.mentioned_files, (
                f"[{fixture.id}] Expected '{expected_file}' in mentioned_files, "
                f"got {facts.mentioned_files}"
            )

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_output_is_issue_facts(self, fixture: IssueFixture) -> None:
        """extract() always returns an IssueFacts instance."""
        facts = _extract(fixture)
        assert isinstance(facts, IssueFacts)

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_body_preserved(self, fixture: IssueFixture) -> None:
        """The raw body is preserved verbatim in facts.body."""
        facts = _extract(fixture)
        assert facts.body == fixture.body
        assert facts.body_length == len(fixture.body)

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_labels_preserved(self, fixture: IssueFixture) -> None:
        """Labels passed in are preserved verbatim in facts.labels."""
        facts = _extract(fixture)
        assert facts.labels == fixture.labels

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_code_blocks_are_typed(self, fixture: IssueFixture) -> None:
        """code_blocks must be a list of CodeBlock instances."""
        facts = _extract(fixture)
        for block in facts.code_blocks:
            assert isinstance(block, CodeBlock)
            assert isinstance(block.language, str)
            assert isinstance(block.content, str)

    @pytest.mark.parametrize("fixture", ISSUE_CATALOG, ids=[f.id for f in ISSUE_CATALOG])
    def test_serializable(self, fixture: IssueFixture) -> None:
        """to_json_file_dict() must return a plain dict with no Pydantic objects."""
        import json
        facts = _extract(fixture)
        d = facts.to_json_file_dict()
        assert isinstance(d, dict)
        json.dumps(d)  # must not raise


# ---------------------------------------------------------------------------
# 2. Targeted edge-case tests
# ---------------------------------------------------------------------------


class TestIssueExtractorEdgeCases:
    """Pin specific behaviours that are easy to accidentally regress."""

    def test_empty_body_does_not_crash(self) -> None:
        facts = IssueExtractor().extract(title="Title", body="")
        assert facts.issue_title == "Title"
        assert facts.body == ""
        assert facts.body_length == 0
        assert facts.mentioned_files == []
        assert facts.code_blocks == []
        assert facts.has_traceback is False
        assert facts.has_reproduction_steps is False

    def test_none_body_treated_as_empty(self) -> None:
        facts = IssueExtractor().extract(title="T", body=None)  # type: ignore[arg-type]
        assert facts.body == ""

    def test_label_takes_priority_over_title_keyword(self) -> None:
        """'enhancement' label wins over bug keyword in title."""
        facts = IssueExtractor().extract(
            title="Fix broken login behaviour",
            body="Please add a better fix.",
            labels=["enhancement"],
        )
        assert facts.issue_type == "feature"

    def test_question_requires_question_mark_in_title(self) -> None:
        """question keyword alone (without '?') must NOT classify as question."""
        facts = IssueExtractor().extract(
            title="How we handle errors in the service",
            body="This is a bug report.",
            labels=[],
        )
        # No '?' in title → bug keywords may win, or "unknown"
        assert facts.issue_type != "question"

    def test_question_with_mark_and_keyword(self) -> None:
        """'how …?' with question label → type=question."""
        facts = IssueExtractor().extract(
            title="How do I configure the plugin?",
            body="Just a question.",
            labels=["question"],
        )
        assert facts.issue_type == "question"

    def test_python_traceback_in_code_block_extracted(self) -> None:
        body = (
            "Here is the error:\n\n"
            "```\nTraceback (most recent call last):\n"
            "  File 'app.py', line 5, in main\n"
            "    raise ValueError('oops')\n"
            "ValueError: oops\n```"
        )
        facts = IssueExtractor().extract(title="Error", body=body, labels=["bug"])
        assert facts.has_traceback is True
        assert len(facts.tracebacks) == 1
        assert "Traceback" in facts.tracebacks[0]

    def test_rust_traceback_in_code_block_extracted(self) -> None:
        body = (
            "Server crashes:\n\n"
            "```\nthread 'main' panicked at 'index out of bounds', src/lib.rs:42\n```"
        )
        facts = IssueExtractor().extract(title="Panic", body=body, labels=[])
        assert facts.has_traceback is True
        assert len(facts.tracebacks) == 1

    def test_traceback_in_raw_body_detected(self) -> None:
        """A traceback not inside a fenced block is still detected."""
        body = (
            "Error:\n"
            "Traceback (most recent call last):\n"
            "  File 'x.py', line 1, in foo\n"
            "RuntimeError: bad\n"
        )
        facts = IssueExtractor().extract(title="Crash", body=body, labels=[])
        assert facts.has_traceback is True

    def test_multiple_code_blocks_captured(self) -> None:
        body = "```python\nprint('hello')\n```\n\n```bash\ncurl http://example.com\n```"
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert len(facts.code_blocks) == 2
        assert facts.code_blocks[0].language == "python"
        assert facts.code_blocks[1].language == "bash"

    def test_checklist_captures_both_checked_and_unchecked(self) -> None:
        body = "- [ ] Open item\n- [x] Done item\n- [X] Also done\n"
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert len(facts.checklist_items) == 3

    def test_file_mention_regex_finds_nested_path(self) -> None:
        body = "See `app/controllers/api/v1/users_controller.rb` for details."
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert "app/controllers/api/v1/users_controller.rb" in facts.mentioned_files

    def test_file_mention_regex_finds_ts_file(self) -> None:
        body = "The bug is in `components/Dashboard.tsx`."
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert "components/Dashboard.tsx" in facts.mentioned_files

    def test_issue_ref_extraction(self) -> None:
        body = "Related to #42 and #1001, see also #87."
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert 42 in facts.referenced_issues
        assert 1001 in facts.referenced_issues
        assert 87 in facts.referenced_issues

    def test_section_headers_extracted(self) -> None:
        body = "## Steps to Reproduce\n\n1. Do X.\n\n## Expected Behavior\n\nFoo."
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert "Steps to Reproduce" in facts.section_headers
        assert "Expected Behavior" in facts.section_headers

    def test_conventional_commit_prefix_fix(self) -> None:
        facts = IssueExtractor().extract(title="fix: broken login redirect", body="")
        assert facts.issue_type == "bug"

    def test_conventional_commit_prefix_feat(self) -> None:
        facts = IssueExtractor().extract(title="feat: add OAuth2 support", body="")
        assert facts.issue_type == "feature"

    def test_conventional_commit_prefix_docs(self) -> None:
        facts = IssueExtractor().extract(title="docs: update README examples", body="")
        assert facts.issue_type == "docs"

    def test_docs_label_maps_to_docs(self) -> None:
        facts = IssueExtractor().extract(
            title="Something about docs", body="", labels=["documentation"]
        )
        assert facts.issue_type == "docs"

    def test_security_label_maps_to_bug(self) -> None:
        facts = IssueExtractor().extract(
            title="Security issue", body="", labels=["security"]
        )
        assert facts.issue_type == "bug"

    def test_performance_label_maps_to_perf(self) -> None:
        facts = IssueExtractor().extract(
            title="App feels slow", body="", labels=["performance"]
        )
        assert facts.issue_type == "perf"

    def test_no_label_unknown_title(self) -> None:
        facts = IssueExtractor().extract(
            title="Integrate the X protocol", body="", labels=[]
        )
        # "Integrate" is not in any keyword set → unknown or feature
        # At minimum it must not crash.
        assert facts.issue_type in (
            "bug", "feature", "docs", "perf", "refactor", "question", "unknown"
        )

    def test_extracted_at_is_set(self) -> None:
        facts = IssueExtractor().extract(title="T", body="")
        assert facts.extracted_at != ""

    def test_issue_url_and_number_passed_through(self) -> None:
        facts = IssueExtractor().extract(
            title="T",
            body="",
            issue_url="https://github.com/owner/repo/issues/42",
            issue_number=42,
        )
        assert facts.issue_url == "https://github.com/owner/repo/issues/42"
        assert facts.issue_number == 42

    def test_reproduction_detected_via_numbered_list(self) -> None:
        """A numbered list without a fenced code block → has_reproduction_steps=True."""
        body = "1. Open the app.\n2. Click login.\n3. Observe crash.\n"
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert facts.has_reproduction_steps is True

    def test_no_reproduction_for_plain_paragraph(self) -> None:
        """A plain prose paragraph with no code block, numbered list, or repro header."""
        body = "The app does not work correctly on Windows 11.\n"
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert facts.has_reproduction_steps is False

    def test_body_with_java_traceback(self) -> None:
        body = (
            "```\n"
            'Exception in thread "main" java.lang.NullPointerException\n'
            "\tat com.example.App.main(App.java:10)\n"
            "```"
        )
        facts = IssueExtractor().extract(title="T", body=body, labels=["bug"])
        assert facts.has_traceback is True

    def test_body_with_go_panic(self) -> None:
        body = "```\ngoroutine 1 [running]:\nmain.main()\n\t/app/main.go:25 +0x58\n```"
        facts = IssueExtractor().extract(title="T", body=body, labels=[])
        assert facts.has_traceback is True


# ---------------------------------------------------------------------------
# 3. GitHubService URL parsing (deterministic, no network)
# ---------------------------------------------------------------------------


class TestGitHubServiceURLParsing:
    """All tests are pure-function / regex — zero network calls."""

    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://github.com/pallets/flask/issues/5421",
                ("pallets", "flask", 5421),
            ),
            (
                "https://github.com/django/django/issues/1",
                ("django", "django", 1),
            ),
            (
                "https://github.com/rust-lang/rust/issues/99999",
                ("rust-lang", "rust", 99999),
            ),
            (
                # .git suffix on repo name should NOT appear in issue URLs, but
                # test that the pattern still handles edge cases gracefully.
                "https://github.com/owner/my.repo/issues/7",
                ("owner", "my.repo", 7),
            ),
            (
                # PR URL — same pattern, should also parse.
                "https://github.com/pallets/flask/pull/200",
                ("pallets", "flask", 200),
            ),
        ],
    )
    def test_parse_valid_issue_url(
        self, url: str, expected: tuple[str, str, int]
    ) -> None:
        assert GitHubService.parse_issue_url(url) == expected

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://github.com/pallets/flask",          # no issue path
            "https://github.com/pallets/flask/issues/",  # no number
            "https://gitlab.com/owner/repo/issues/1",    # wrong host
            "not-a-url-at-all",
            "",
        ],
    )
    def test_parse_invalid_url_raises_value_error(self, bad_url: str) -> None:
        with pytest.raises(ValueError):
            GitHubService.parse_issue_url(bad_url)

    def test_issue_labels_helper(self) -> None:
        raw = {"labels": [{"name": "bug"}, {"name": "good first issue"}]}
        labels = GitHubService.issue_labels(raw)
        assert labels == ["bug", "good first issue"]

    def test_issue_labels_empty(self) -> None:
        assert GitHubService.issue_labels({}) == []
        assert GitHubService.issue_labels({"labels": []}) == []


# ---------------------------------------------------------------------------
# 4. GitHubService fetch_issue validation
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, json_data=None, status_code=200, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text
        self.is_success = 200 <= status_code < 300

    def json(self):
        return self._json


class TestGitHubServiceFetchIssueValidation:
    """Mocked network tests for fetch_issue actionability guards."""

    def test_closed_issue_raises(self, monkeypatch) -> None:
        """Closed issue with state_reason → IssueNotActionableError."""
        def fake_get(url, **kwargs):
            return _FakeResponse(
                json_data={
                    "state": "closed",
                    "state_reason": "not_planned",
                    "title": "Old bug",
                    "number": 42,
                }
            )

        monkeypatch.setattr("openforge.services.github.httpx.get", fake_get)
        svc = GitHubService()
        with pytest.raises(IssueNotActionableError) as exc_info:
            svc.fetch_issue("owner", "repo", 42)
        assert "state='closed'" in str(exc_info.value)
        assert "reason: not_planned" in str(exc_info.value)

    def test_pr_url_raises(self, monkeypatch) -> None:
        """PR masquerading as issue URL → IssueNotActionableError."""
        def fake_get(url, **kwargs):
            return _FakeResponse(
                json_data={
                    "state": "open",
                    "title": "Feature PR",
                    "number": 99,
                    "pull_request": {"url": "https://api.github.com/repos/owner/repo/pulls/99"},
                }
            )

        monkeypatch.setattr("openforge.services.github.httpx.get", fake_get)
        svc = GitHubService()
        with pytest.raises(IssueNotActionableError) as exc_info:
            svc.fetch_issue("owner", "repo", 99)
        assert "is a pull request, not an issue" in str(exc_info.value)

    def test_open_issue_with_linked_merged_pr_raises(self, monkeypatch) -> None:
        """Technically open but linked merged fix → IssueNotActionableError."""
        timeline_url = "https://api.github.com/repos/owner/repo/issues/7/timeline"

        def fake_get(url, **kwargs):
            if url.endswith("/issues/7"):
                return _FakeResponse(
                    json_data={
                        "state": "open",
                        "title": "Bug",
                        "number": 7,
                        "timeline_url": timeline_url,
                    }
                )
            if url == timeline_url:
                return _FakeResponse(
                    json_data=[
                        {
                            "event": "cross-referenced",
                            "source": {
                                "issue": {
                                    "pull_request": {},
                                    "html_url": "https://github.com/owner/repo/pull/8",
                                    "state": "closed",
                                    "merged": True,
                                    "merged_at": "2024-01-01T00:00:00Z",
                                }
                            },
                        }
                    ]
                )
            return _FakeResponse(status_code=404)

        monkeypatch.setattr("openforge.services.github.httpx.get", fake_get)
        svc = GitHubService()
        with pytest.raises(IssueNotActionableError) as exc_info:
            svc.fetch_issue("owner", "repo", 7)
        assert "addressed by a merged pull request" in str(exc_info.value)
        assert "https://github.com/owner/repo/pull/8" in str(exc_info.value)

    def test_genuinely_open_issue_succeeds(self, monkeypatch) -> None:
        """Open issue without linked merged PR returns raw dict."""
        timeline_url = "https://api.github.com/repos/owner/repo/issues/3/timeline"

        def fake_get(url, **kwargs):
            if url.endswith("/issues/3"):
                return _FakeResponse(
                    json_data={
                        "state": "open",
                        "title": "Real bug",
                        "number": 3,
                        "timeline_url": timeline_url,
                    }
                )
            if url == timeline_url:
                return _FakeResponse(json_data=[])
            return _FakeResponse(status_code=404)

        monkeypatch.setattr("openforge.services.github.httpx.get", fake_get)
        svc = GitHubService()
        raw = svc.fetch_issue("owner", "repo", 3)
        assert raw["state"] == "open"
        assert raw["title"] == "Real bug"


# ---------------------------------------------------------------------------
# 5. Artifact System, Checksum & Manifest tests
# ---------------------------------------------------------------------------


class TestArtifactSystem:
    """Verify generic Artifact container, tool_version, SHA-256 checksums, and manifest."""

    def test_artifact_wrap_and_serialization(self) -> None:
        from openforge import __version__
        from openforge.models import Artifact, IssueFacts, SourceArtifact, compute_sha256

        facts = IssueFacts(
            issue_title="Bug report",
            issue_type="bug",
            body="Help me",
        )
        src_art = SourceArtifact(
            artifact="RepositoryContext",
            file_name="repository.json",
            run_id="run-1234",
            checksum="sha256:abc12345",
        )
        artifact_obj = Artifact.wrap(
            data=facts,
            generator="openforge.services.issue_extractor.IssueExtractor",
            run_id="run-1234",
            source_artifacts=[src_art],
        )

        dict_out = artifact_obj.to_json_file_dict()
        assert dict_out["schema_version"] == 1
        assert dict_out["tool_version"] == __version__
        assert dict_out["artifact"] == "IssueFacts"
        assert dict_out["generator"] == "openforge.services.issue_extractor.IssueExtractor"
        assert dict_out["data_checksum"] == compute_sha256(facts)
        assert dict_out["data_checksum"].startswith("sha256:")
        assert dict_out["provenance"]["run_id"] == "run-1234"

        src = dict_out["provenance"]["source_artifacts"][0]
        assert src["artifact"] == "RepositoryContext"
        assert src["file_name"] == "repository.json"
        assert src["run_id"] == "run-1234"
        assert src["checksum"] == "sha256:abc12345"

    def test_repository_context_wrapping_and_checksum(self) -> None:
        from openforge.models import Artifact, RepositoryContext, compute_sha256

        ctx = RepositoryContext(repo="pallets/flask", source_path="/tmp/flask")
        artifact_obj = Artifact.wrap(
            data=ctx,
            generator="openforge.services.repository_analyzer.RepositoryAnalyzer",
            run_id="run-5678",
        )

        dict_out = artifact_obj.to_json_file_dict()
        assert dict_out["artifact"] == "RepositoryContext"
        assert dict_out["data_checksum"] == compute_sha256(ctx)
        assert dict_out["provenance"]["run_id"] == "run-5678"
        assert dict_out["provenance"]["source_artifacts"] == []
        assert dict_out["data"]["repo"] == "pallets/flask"

    def test_run_manifest_serialization(self) -> None:
        from openforge import __version__
        from openforge.models import RunManifest

        manifest = RunManifest(
            run_id="2026-07-21T17-00-00",
            target="psf/requests",
            artifacts=["repository.json", "issue_facts.json"],
        )
        d = manifest.to_json_file_dict()
        assert d["run_id"] == "2026-07-21T17-00-00"
        assert d["target"] == "psf/requests"
        assert d["tool_version"] == __version__
        assert d["artifacts"] == ["repository.json", "issue_facts.json"]
        assert "started_at" in d
        assert "updated_at" in d


# ---------------------------------------------------------------------------
# 5. Milestone 4 Planning Contract & Catalog tests
# ---------------------------------------------------------------------------


class TestMilestone4Models:
    """Verify ImplementationPlan model schema, catalog fixtures, and serialisability."""

    def test_implementation_plan_contract(self) -> None:
        from openforge.models import Artifact, ImplementationPlan, SourceArtifact, EditTarget

        plan = ImplementationPlan(
            goal="Fix 422 Unprocessable Entity in /api/users",
            edit_targets=[EditTarget(file_path="routers/users.py")],
            implementation_steps=[
                "Parse nested JSON address parameter using Pydantic model",
                "Update type annotation in create_user route handler",
            ],
            tests_to_run=["pytest tests/test_users.py"],
            risks=["Breaking change for old API clients submitting flat address string"],
            requires_human=False,
        )

        sources = [
            SourceArtifact(artifact="RepositoryContext", file_name="repository.json"),
            SourceArtifact(artifact="IssueFacts", file_name="issue_facts.json"),
        ]
        artifact_obj = Artifact.wrap(
            data=plan,
            generator="openforge.agents.planner.PlannerAgent",
            run_id="run-9999",
            source_artifacts=sources,
        )

        dict_out = artifact_obj.to_json_file_dict()
        assert dict_out["artifact"] == "ImplementationPlan"
        assert dict_out["generator"] == "openforge.agents.planner.PlannerAgent"
        assert len(dict_out["provenance"]["source_artifacts"]) == 2
        assert dict_out["data"]["goal"].startswith("Fix 422")
        assert len(dict_out["data"]["edit_targets"]) == 1

    def test_planning_catalog_fixtures(self) -> None:
        from tests.planning_catalog import PLANNING_CATALOG, PlanningFixture

        assert len(PLANNING_CATALOG) >= 3
        for fixture in PLANNING_CATALOG:
            assert isinstance(fixture, PlanningFixture)
            assert fixture.expected_plan.goal != ""
            d = fixture.expected_plan.to_json_file_dict()
            assert isinstance(d, dict)


