"""OpenForge CLI.

Commands
────────
openforge inspect        Milestone 2 — analyze a repository → repository.json
openforge analyze-issue  Milestone 3 — extract issue facts  → issue_facts.json
openforge plan           Milestone 4 — build an implementation plan → plan.json
openforge code           Milestone 5 — generate code changes → code.json
openforge version        Print version.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from openforge import __version__
from openforge.config import configure_logging, get_settings
from openforge.agents.coder import CoderError, CodingAgent
from openforge.agents.planner import PlannerAgent, PlannerError
from openforge.models import (
    Artifact,
    ImplementationPlan,
    IssueFacts,
    RepositoryContext,
    RunManifest,
    SourceArtifact,
    compute_sha256,
    verify_artifact_file,
)
from openforge.services.github import GitHubService
from openforge.services.issue_extractor import IssueExtractor
from openforge.services.openrouter import summarize_conventions
from openforge.services.repository_analyzer import RepositoryAnalyzer

app = typer.Typer(
    name="openforge",
    help="AI agent that automates the open-source contribution workflow, "
    "with a human approval gate before anything is submitted.",
    add_completion=False,
)
console = Console()
logger = logging.getLogger(__name__)

_REPO_ANALYZER_FQCN = "openforge.services.repository_analyzer.RepositoryAnalyzer"
_ISSUE_EXTRACTOR_FQCN = "openforge.services.issue_extractor.IssueExtractor"
_PLANNER_FQCN = "openforge.agents.planner.PlannerAgent"
_CODER_FQCN = "openforge.agents.coder.CodingAgent"


def _update_manifest(run_dir: Path, target: str | None, new_artifacts: list[str]) -> None:
    """Create or update manifest.json in the run directory."""
    manifest_path = run_dir / "manifest.json"
    now = datetime.now(tz=timezone.utc).isoformat()
    if manifest_path.exists():
        try:
            data = json.loads(manifest_path.read_text())
            manifest = RunManifest(**data)
            manifest.updated_at = now
            for art in new_artifacts:
                if art not in manifest.artifacts:
                    manifest.artifacts.append(art)
        except Exception:
            manifest = RunManifest(
                run_id=run_dir.name,
                target=target,
                started_at=now,
                updated_at=now,
                artifacts=new_artifacts,
            )
    else:
        manifest = RunManifest(
            run_id=run_dir.name,
            target=target,
            started_at=now,
            updated_at=now,
            artifacts=new_artifacts,
        )
    manifest_path.write_text(json.dumps(manifest.to_json_file_dict(), indent=2) + "\n")


# ---------------------------------------------------------------------------
# openforge inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    target: str = typer.Argument(
        ...,
        help="A GitHub repo URL, a GitHub issue URL (repo is extracted from it), "
        "or a path to a repository already on disk.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Skip the optional conventions summary even if an API key is set.",
    ),
) -> None:
    """Run the RepositoryAnalyzer against TARGET and print what it found."""
    settings = get_settings()
    configure_logging(settings.log_level)

    analyzer = RepositoryAnalyzer(settings.workspace_dir)

    with console.status(f"[bold cyan]Resolving {target}..."):
        try:
            local_path, repo_label = analyzer.resolve(target)
        except (FileNotFoundError, RuntimeError) as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(code=1)

    with console.status("[bold cyan]Detecting language, tooling, and conventions..."):
        context = analyzer.analyze(local_path, repo_label)

        conventions: str | None = None
        if not no_llm:
            conventions = summarize_conventions(context, settings)
            context.conventions_summary = conventions

    _print_context(context)

    # ---- write run artifacts -----------------------------------------------
    run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    run_dir = settings.runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    repo_artifact = Artifact.wrap(
        data=context,
        generator=_REPO_ANALYZER_FQCN,
        run_id=run_id,
        source_artifacts=[],
    )
    repo_json = run_dir / "repository.json"
    repo_json.write_text(json.dumps(repo_artifact.to_json_file_dict(), indent=2) + "\n")
    console.print(f"\n[green]✓[/green] Repository analyzed")

    written: list[Path] = [repo_json]

    if conventions:
        summary_md = run_dir / "summary.md"
        summary_md.write_text(conventions + "\n")
        written.append(summary_md)

    logs_txt = run_dir / "logs.txt"
    logs_txt.write_text(
        f"run_id: {run_id}\n"
        f"target: {target}\n"
        f"repo: {context.repo}\n"
        f"language: {context.language}\n"
        f"framework: {context.framework}\n"
        f"package_manager: {context.package_manager}\n"
        f"test_command: {context.test_command}\n"
        f"formatter: {context.formatter}\n"
        f"linter: {context.linter}\n"
        f"ci: {context.ci}\n"
    )
    written.append(logs_txt)

    _update_manifest(run_dir, target, [p.name for p in written])

    # ---- print the runs/ tree ----------------------------------------------
    written_files = sorted(p for p in run_dir.iterdir() if p.is_file())
    console.print(f"\n[bold]runs/[/bold]")
    console.print(f"└── [cyan]{run_id}/[/cyan]")
    for i, p in enumerate(written_files):
        prefix = "    └──" if i == len(written_files) - 1 else "    ├──"
        console.print(f"{prefix} {p.name}")


# ---------------------------------------------------------------------------
# openforge analyze-issue
# ---------------------------------------------------------------------------


def _load_repository_context(repo_json_path: Path, no_verify: bool = False) -> RepositoryContext:
    """Load RepositoryContext from json, handling both raw and Artifact-wrapped JSON."""
    if not no_verify:
        verify_artifact_file(repo_json_path)
    raw = json.loads(repo_json_path.read_text())
    if "data" in raw and isinstance(raw["data"], dict) and "repo" in raw["data"]:
        return RepositoryContext(**raw["data"])
    return RepositoryContext(**raw)


@app.command("analyze-issue")
def analyze_issue(
    issue_url: str = typer.Argument(
        ...,
        help="Full GitHub issue URL, e.g. https://github.com/pallets/flask/issues/5421",
    ),
    run: str | None = typer.Option(
        None,
        "--run",
        help="Existing run ID whose repository.json to reuse (skips re-cloning).",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip artifact checksum verification.",
    ),
) -> None:
    """Fetch a GitHub issue and extract structured facts → issue_facts.json."""
    settings = get_settings()
    configure_logging(settings.log_level)

    # 1. Parse the issue URL — fully deterministic.
    try:
        owner, repo_name, issue_number = GitHubService.parse_issue_url(issue_url)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    repo_slug = f"{owner}/{repo_name}"
    console.print(f"[dim]Issue:[/dim] [bold]{repo_slug}#{issue_number}[/bold]")

    # 2. Load or build RepositoryContext (for the run directory).
    repo_context: RepositoryContext | None = None
    run_dir: Path
    rich_source_artifacts: list[SourceArtifact] = []

    if run:
        run_dir = settings.runs_dir / run
        repo_json_path = run_dir / "repository.json"
        if not repo_json_path.exists():
            console.print(
                f"[bold red]Error:[/bold red] No repository.json found in runs/{run}/. "
                "Run `openforge inspect` first, or omit --run."
            )
            raise typer.Exit(code=1)
        try:
            repo_context = _load_repository_context(repo_json_path, no_verify=no_verify)
        except ValueError as exc:
            console.print(f"[bold red]Verification Error:[/bold red] {exc}")
            raise typer.Exit(code=1)
        rich_source_artifacts.append(
            SourceArtifact(
                artifact="RepositoryContext",
                file_name="repository.json",
                run_id=run,
                checksum=compute_sha256(repo_context),
            )
        )
        console.print(f"[green]✓[/green] Loaded repository context from runs/{run}/")
    else:
        # Create a new timestamped run directory.
        run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        run_dir = settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        # Clone (or reuse workspace clone) + analyze.
        repo_url = f"https://github.com/{repo_slug}"
        analyzer = RepositoryAnalyzer(settings.workspace_dir)
        with console.status(f"[bold cyan]Analyzing repository {repo_slug}..."):
            try:
                local_path, repo_label = analyzer.resolve(repo_url)
                repo_context = analyzer.analyze(local_path, repo_label)
            except (FileNotFoundError, RuntimeError) as exc:
                console.print(
                    f"[yellow]Warning:[/yellow] Repository analysis failed ({exc}); "
                    "proceeding without repo context."
                )
                repo_context = None

        if repo_context:
            repo_artifact = Artifact.wrap(
                data=repo_context,
                generator=_REPO_ANALYZER_FQCN,
                run_id=run_id,
                source_artifacts=[],
            )
            (run_dir / "repository.json").write_text(
                json.dumps(repo_artifact.to_json_file_dict(), indent=2) + "\n"
            )
            rich_source_artifacts.append(
                SourceArtifact(
                    artifact="RepositoryContext",
                    file_name="repository.json",
                    run_id=run_id,
                    checksum=compute_sha256(repo_context),
                )
            )
            console.print(f"[green]✓[/green] Repository analyzed")

    # 3. Fetch the GitHub issue (requires network; GITHUB_TOKEN recommended).
    github = GitHubService(token=settings.github_token)
    with console.status(f"[bold cyan]Fetching issue #{issue_number}..."):
        try:
            raw_issue = github.fetch_issue(owner, repo_name, issue_number)
        except (PermissionError, FileNotFoundError, RuntimeError) as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(code=1)

    title: str = raw_issue.get("title", "")
    body: str = raw_issue.get("body") or ""
    labels: list[str] = GitHubService.issue_labels(raw_issue)
    console.print(f"[green]✓[/green] Issue fetched: {title!r}")

    # 4. Extract facts — deterministic, no LLM.
    extractor = IssueExtractor()
    with console.status("[bold cyan]Extracting issue facts..."):
        facts = extractor.extract(
            title=title,
            body=body,
            labels=labels,
            issue_url=issue_url,
            issue_number=issue_number,
        )
    console.print(f"[green]✓[/green] Facts extracted")

    # 5. Write issue_facts.json wrapped in Artifact container with rich provenance.
    facts_artifact = Artifact.wrap(
        data=facts,
        generator=_ISSUE_EXTRACTOR_FQCN,
        run_id=run_dir.name,
        source_artifacts=rich_source_artifacts,
    )
    facts_json = run_dir / "issue_facts.json"
    facts_json.write_text(json.dumps(facts_artifact.to_json_file_dict(), indent=2) + "\n")
    console.print(f"[green]✓[/green] Issue analyzed")

    # 6. Append to logs.txt.
    logs_txt = run_dir / "logs.txt"
    log_lines = (
        f"issue_url: {issue_url}\n"
        f"issue_number: {issue_number}\n"
        f"issue_type: {facts.issue_type}\n"
        f"has_traceback: {facts.has_traceback}\n"
        f"has_reproduction_steps: {facts.has_reproduction_steps}\n"
        f"mentioned_files: {len(facts.mentioned_files)}\n"
        f"checklist_items: {len(facts.checklist_items)}\n"
    )
    if logs_txt.exists():
        with logs_txt.open("a") as f:
            f.write("\n" + log_lines)
    else:
        logs_txt.write_text(log_lines)

    # 7. Update manifest.json.
    _update_manifest(run_dir, issue_url, ["repository.json", "issue_facts.json", "logs.txt"])

    # 8. Print summary table.
    _print_issue_facts(facts)

    # 9. Print runs/ tree.
    written_files = sorted(p for p in run_dir.iterdir() if p.is_file())
    console.print(f"\n[bold]runs/[/bold]")
    console.print(f"└── [cyan]{run_dir.name}/[/cyan]")
    for i, p in enumerate(written_files):
        prefix = "    └──" if i == len(written_files) - 1 else "    ├──"
        console.print(f"{prefix} {p.name}")


# ---------------------------------------------------------------------------
# openforge plan
# ---------------------------------------------------------------------------


@app.command()
def plan(
    issue_url: str = typer.Argument(
        ...,
        help="Full GitHub issue URL, e.g. https://github.com/pallets/flask/issues/5421",
    ),
    run: str | None = typer.Option(
        None,
        "--run",
        help="Existing run ID whose repository.json and issue_facts.json to reuse.",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip artifact checksum verification.",
    ),
) -> None:
    """Generate an ImplementationPlan for a GitHub issue → plan.json."""
    settings = get_settings()
    configure_logging(settings.log_level)

    # --- Fail fast on missing API key before doing any network work ----------
    if not settings.llm_enabled:
        console.print(
            "[bold red]Error:[/bold red] OPENROUTER_API_KEY is not set.\n"
            "The Planner requires an LLM. Export the key and retry:\n\n"
            "  export OPENROUTER_API_KEY=sk-or-..."
        )
        raise typer.Exit(code=1)

    # --- Parse issue URL (deterministic) ------------------------------------
    try:
        owner, repo_name, issue_number = GitHubService.parse_issue_url(issue_url)
    except ValueError as exc:
        console.print(f"[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    repo_slug = f"{owner}/{repo_name}"
    console.print(f"[dim]Planning:[/dim] [bold]{repo_slug}#{issue_number}[/bold]")

    repo_context: RepositoryContext | None = None
    issue_facts: IssueFacts | None = None
    run_dir: Path
    rich_source_artifacts: list[SourceArtifact] = []

    if run:
        # Reuse artifacts from an existing run.
        run_dir = settings.runs_dir / run
        repo_json_path = run_dir / "repository.json"
        facts_json_path = run_dir / "issue_facts.json"

        if repo_json_path.exists():
            try:
                repo_context = _load_repository_context(repo_json_path, no_verify=no_verify)
            except ValueError as exc:
                console.print(f"[bold red]Verification Error:[/bold red] {exc}")
                raise typer.Exit(code=1)
            rich_source_artifacts.append(
                SourceArtifact(
                    artifact="RepositoryContext",
                    file_name="repository.json",
                    run_id=run,
                    checksum=compute_sha256(repo_context),
                )
            )
            console.print(f"[green]✓[/green] Loaded repository context from runs/{run}/")
        else:
            console.print(
                f"[yellow]Warning:[/yellow] No repository.json in runs/{run}/; "
                "proceeding without repo context."
            )

        if facts_json_path.exists():
            if not no_verify:
                try:
                    verify_artifact_file(facts_json_path)
                except ValueError as exc:
                    console.print(f"[bold red]Verification Error:[/bold red] {exc}")
                    raise typer.Exit(code=1)
            raw = json.loads(facts_json_path.read_text())
            facts_data = raw.get("data", raw)
            issue_facts = IssueFacts(**facts_data)
            rich_source_artifacts.append(
                SourceArtifact(
                    artifact="IssueFacts",
                    file_name="issue_facts.json",
                    run_id=run,
                    checksum=compute_sha256(issue_facts),
                )
            )
            console.print(f"[green]✓[/green] Loaded issue facts from runs/{run}/")
        else:
            console.print(
                f"[bold red]Error:[/bold red] No issue_facts.json in runs/{run}/. "
                "Run `openforge analyze-issue` first, or omit --run."
            )
            raise typer.Exit(code=1)
    else:
        # Fresh run: clone repo + fetch + extract.
        run_id = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        run_dir = settings.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        repo_url = f"https://github.com/{repo_slug}"
        analyzer = RepositoryAnalyzer(settings.workspace_dir)
        with console.status(f"[bold cyan]Analyzing repository {repo_slug}..."):
            try:
                local_path, repo_label = analyzer.resolve(repo_url)
                repo_context = analyzer.analyze(local_path, repo_label)
            except (FileNotFoundError, RuntimeError) as exc:
                console.print(
                    f"[yellow]Warning:[/yellow] Repository analysis failed ({exc}); "
                    "proceeding without repo context."
                )
                repo_context = None

        if repo_context:
            repo_artifact = Artifact.wrap(
                data=repo_context,
                generator=_REPO_ANALYZER_FQCN,
                run_id=run_id,
                source_artifacts=[],
            )
            (run_dir / "repository.json").write_text(
                json.dumps(repo_artifact.to_json_file_dict(), indent=2) + "\n"
            )
            rich_source_artifacts.append(
                SourceArtifact(
                    artifact="RepositoryContext",
                    file_name="repository.json",
                    run_id=run_id,
                    checksum=compute_sha256(repo_context),
                )
            )
            console.print(f"[green]✓[/green] Repository analyzed")

        # Fetch + extract issue.
        github = GitHubService(token=settings.github_token)
        with console.status(f"[bold cyan]Fetching issue #{issue_number}..."):
            try:
                raw_issue = github.fetch_issue(owner, repo_name, issue_number)
            except (PermissionError, FileNotFoundError, RuntimeError) as exc:
                console.print(f"[bold red]Error:[/bold red] {exc}")
                raise typer.Exit(code=1)

        title: str = raw_issue.get("title", "")
        body: str = raw_issue.get("body") or ""
        labels: list[str] = GitHubService.issue_labels(raw_issue)
        console.print(f"[green]✓[/green] Issue fetched: {title!r}")

        extractor = IssueExtractor()
        with console.status("[bold cyan]Extracting issue facts..."):
            issue_facts = extractor.extract(
                title=title,
                body=body,
                labels=labels,
                issue_url=issue_url,
                issue_number=issue_number,
            )

        facts_artifact = Artifact.wrap(
            data=issue_facts,
            generator=_ISSUE_EXTRACTOR_FQCN,
            run_id=run_dir.name,
            source_artifacts=rich_source_artifacts[:],
        )
        facts_json_path = run_dir / "issue_facts.json"
        facts_json_path.write_text(
            json.dumps(facts_artifact.to_json_file_dict(), indent=2) + "\n"
        )
        rich_source_artifacts.append(
            SourceArtifact(
                artifact="IssueFacts",
                file_name="issue_facts.json",
                run_id=run_dir.name,
                checksum=compute_sha256(issue_facts),
            )
        )
        console.print(f"[green]✓[/green] Issue facts extracted")

    # --- Call PlannerAgent (LLM step) ---------------------------------------
    assert issue_facts is not None  # guaranteed by logic above
    planner = PlannerAgent(settings)
    with console.status("[bold cyan]Generating implementation plan (LLM)..."):
        try:
            impl_plan = planner.plan(repo_context, issue_facts)
        except PlannerError as exc:
            console.print(f"[bold red]Planner error:[/bold red] {exc}")
            raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Plan generated")

    # --- Write plan.json artifact -------------------------------------------
    plan_artifact = Artifact.wrap(
        data=impl_plan,
        generator=_PLANNER_FQCN,
        run_id=run_dir.name,
        source_artifacts=rich_source_artifacts,
    )
    plan_json = run_dir / "plan.json"
    plan_json.write_text(json.dumps(plan_artifact.to_json_file_dict(), indent=2) + "\n")

    # --- Update manifest ----------------------------------------------------
    _update_manifest(
        run_dir,
        issue_url,
        ["repository.json", "issue_facts.json", "plan.json"],
    )

    # --- Print human-readable plan ------------------------------------------
    _print_plan(impl_plan)

    # --- Print runs/ tree ---------------------------------------------------
    written_files = sorted(p for p in run_dir.iterdir() if p.is_file())
    console.print(f"\n[bold]runs/[/bold]")
    console.print(f"└── [cyan]{run_dir.name}/[/cyan]")
    for i, p in enumerate(written_files):
        prefix = "    └──" if i == len(written_files) - 1 else "    ├──"
        console.print(f"{prefix} {p.name}")


# ---------------------------------------------------------------------------
# openforge code
# ---------------------------------------------------------------------------


@app.command()
def code(
    run: str = typer.Option(
        ...,
        "--run",
        help="Run ID whose repository.json and plan.json to use.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force execution even if the plan requires human sign-off.",
    ),
    no_verify: bool = typer.Option(
        False,
        "--no-verify",
        help="Skip artifact checksum verification.",
    ),
) -> None:
    """Generate code from an ImplementationPlan and create a local git branch → code.json."""
    settings = get_settings()
    configure_logging(settings.log_level)

    if not settings.llm_enabled:
        console.print(
            "[bold red]Error:[/bold red] OPENROUTER_API_KEY is not set.\n"
            "The CodingAgent requires an LLM. Export the key and retry:\n\n"
            "  export OPENROUTER_API_KEY=sk-or-..."
        )
        raise typer.Exit(code=1)

    run_dir = settings.runs_dir / run

    # --- Load repository.json -----------------------------------------------
    repo_json_path = run_dir / "repository.json"
    if not repo_json_path.exists():
        console.print(
            f"[bold red]Error:[/bold red] No repository.json found in runs/{run}/. "
            "Run `openforge inspect` or `openforge plan` first."
        )
        raise typer.Exit(code=1)
    try:
        repo_context = _load_repository_context(repo_json_path, no_verify=no_verify)
    except ValueError as exc:
        console.print(f"[bold red]Verification Error:[/bold red] {exc}")
        raise typer.Exit(code=1)
    console.print(f"[green]✓[/green] Loaded repository context from runs/{run}/")

    # --- Load plan.json -----------------------------------------------------
    plan_json_path = run_dir / "plan.json"
    if not plan_json_path.exists():
        console.print(
            f"[bold red]Error:[/bold red] No plan.json found in runs/{run}/. "
            "Run `openforge plan` first."
        )
        raise typer.Exit(code=1)
    
    if not no_verify:
        try:
            verify_artifact_file(plan_json_path)
        except ValueError as exc:
            console.print(f"[bold red]Verification Error:[/bold red] {exc}")
            raise typer.Exit(code=1)

    raw_plan = json.loads(plan_json_path.read_text())
    plan_data = raw_plan.get("data", raw_plan)
    impl_plan = ImplementationPlan(**plan_data)
    console.print(f"[green]✓[/green] Loaded plan from runs/{run}/")

    if impl_plan.requires_human and not force:
        console.print(
            "[bold red]Error:[/bold red] The plan requires human sign-off before coding.\n"
            "Please review the plan.json manually and use --force to proceed."
        )
        raise typer.Exit(code=1)
    elif impl_plan.requires_human and force:
        logger.warning("Proceeding with requires_human=True plan due to --force flag.")

    # --- Derive issue number from plan provenance (best-effort) -------------
    issue_number: int | None = None
    try:
        source_artifacts = raw_plan.get("provenance", {}).get("source_artifacts", [])
        # Load issue_facts.json from this run to get the issue number.
        facts_path = run_dir / "issue_facts.json"
        if facts_path.exists():
            facts_raw = json.loads(facts_path.read_text())
            facts_data = facts_raw.get("data", facts_raw)
            issue_number = facts_data.get("issue_number")
    except Exception:
        pass

    # --- Run CodingAgent (LLM + git + tests) --------------------------------
    agent = CodingAgent(settings)
    console.print("[bold cyan]Generating code and running tests...[/bold cyan]")
    try:
        result = agent.implement(
            repo_context, impl_plan, issue_number=issue_number
        )
    except CoderError as exc:
        console.print(f"[bold red]Coder error:[/bold red] {exc}")
        raise typer.Exit(code=1)

    console.print(f"[green]✓[/green] Code generated")

    # --- Write code.json artifact -------------------------------------------
    # CodeResult is a dataclass, not a Pydantic model, so we build the
    # envelope dict manually following the same shape as other artifacts.
    code_envelope = {
        "schema_version": 1,
        "artifact": "CodeResult",
        "generator": _CODER_FQCN,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "provenance": {
            "run_id": run,
            "generator": _CODER_FQCN,
            "source_artifacts": [
                {
                    "artifact": "ImplementationPlan",
                    "file_name": "plan.json",
                    "run_id": run,
                }
            ],
        },
        "data": result.to_json_file_dict(),
    }
    code_json = run_dir / "code.json"
    code_json.write_text(json.dumps(code_envelope, indent=2) + "\n")

    # --- Update manifest ----------------------------------------------------
    _update_manifest(run_dir, None, ["code.json"])

    # --- Print result -------------------------------------------------------
    _print_code_result(result)

    # --- Print runs/ tree ---------------------------------------------------
    written_files = sorted(p for p in run_dir.iterdir() if p.is_file())
    console.print(f"\n[bold]runs/[/bold]")
    console.print(f"└── [cyan]{run}/[/cyan]")
    for i, p in enumerate(written_files):
        prefix = "    └──" if i == len(written_files) - 1 else "    ├──"
        console.print(f"{prefix} {p.name}")


# ---------------------------------------------------------------------------
# openforge version
# ---------------------------------------------------------------------------


@app.command()
def version() -> None:
    """Print the OpenForge version."""
    console.print(f"openforge {__version__}")


# ---------------------------------------------------------------------------
# Rich helpers
# ---------------------------------------------------------------------------


def _print_context(context) -> None:
    table = Table(title=f"RepositoryAnalyzer report — {context.repo}", show_lines=False)
    table.add_column("Field", style="bold")
    table.add_column("Detected")
    table.add_column("Evidence", style="dim")

    rows = [
        ("language", context.language),
        ("framework", context.framework),
        ("package_manager", context.package_manager),
        ("test_command", context.test_command),
        ("formatter", context.formatter),
        ("linter", context.linter),
        ("ci", context.ci),
    ]
    for field_name, value in rows:
        display = value if value else "[dim]not detected[/dim]"
        evidence = context.evidence.get(field_name, "")
        table.add_row(field_name, display, evidence)

    console.print(table)

    if context.important_directories:
        console.print(
            "\n[bold]Top-level directories:[/bold] "
            + ", ".join(context.important_directories)
        )

    if context.conventions_summary:
        console.print("\n[bold]Conventions summary:[/bold]")
        console.print(context.conventions_summary)
    elif not context.conventions_summary:
        console.print(
            "\n[dim]No conventions summary (set OPENROUTER_API_KEY to enable, "
            "or pass --no-llm to silence this).[/dim]"
        )


def _print_code_result(result) -> None:
    """Print branch name, full git diff, and test results."""
    console.print(f"\n[bold]Branch:[/bold] [cyan]{result.branch_name}[/cyan]")

    if result.unexpected_files:
        console.print("\n[bold yellow]⚠  Unexpected files (outside plan):[/bold yellow]")
        for f in result.unexpected_files:
            console.print(f"  • {f}")

    console.print("\n[bold]Git diff:[/bold]")
    if result.git_diff:
        console.print(result.git_diff)
    else:
        console.print("[dim](no changes detected by git diff)[/dim]")

    for tr in result.test_results:
        status = "[bold green]PASSED[/bold green]" if tr.passed else "[bold red]FAILED[/bold red]"
        console.print(f"\n[bold]Tests:[/bold] {tr.command}  →  {status} (exit {tr.returncode})")
        if tr.stdout:
            console.print(tr.stdout[-4000:])
        if tr.stderr and not tr.passed:
            console.print("[dim]stderr:[/dim]")
            console.print(tr.stderr[-2000:])


def _print_plan(plan: ImplementationPlan) -> None:
    """Print a human-readable summary of the ImplementationPlan."""
    console.print()
    if plan.requires_human:
        console.print(
            "[bold yellow]⚠  requires_human = true[/bold yellow]  "
            "A human must review this plan before code generation proceeds."
        )
    else:
        console.print("[bold green]✓  requires_human = false[/bold green]")

    console.print(f"\n[bold]Goal[/bold]\n{plan.goal}")

    if plan.files_to_modify:
        console.print("\n[bold]Files to modify[/bold]")
        for f in plan.files_to_modify:
            console.print(f"  • {f}")

    if plan.implementation_steps:
        console.print("\n[bold]Implementation steps[/bold]")
        for i, step in enumerate(plan.implementation_steps, 1):
            console.print(f"  {i}. {step}")

    if plan.tests_to_run:
        console.print("\n[bold]Tests to run[/bold]")
        for t in plan.tests_to_run:
            console.print(f"  $ {t}")

    if plan.risks:
        console.print("\n[bold]Risks[/bold]")
        for r in plan.risks:
            console.print(f"  ⚠  {r}")


def _print_issue_facts(facts: IssueFacts) -> None:
    table = Table(
        title=f"IssueExtractor report — #{facts.issue_number or '?'}",
        show_lines=False,
    )
    table.add_column("Field", style="bold")
    table.add_column("Value")

    rows: list[tuple[str, str]] = [
        ("issue_type", facts.issue_type),
        ("has_traceback", "yes" if facts.has_traceback else "no"),
        ("has_reproduction", "yes" if facts.has_reproduction_steps else "no"),
        ("mentioned_files", str(len(facts.mentioned_files))),
        ("code_blocks", str(len(facts.code_blocks))),
        ("checklist_items", str(len(facts.checklist_items))),
        ("referenced_issues", str(len(facts.referenced_issues))),
        ("tracebacks", str(len(facts.tracebacks))),
        ("labels", ", ".join(facts.labels) or "—"),
    ]
    for name, val in rows:
        table.add_row(name, val or "[dim]—[/dim]")

    console.print(table)

    if facts.checklist_items:
        console.print("\n[bold]Checklist items:[/bold]")
        for item in facts.checklist_items:
            console.print(f"  □ {item}")

    if facts.mentioned_files:
        console.print("\n[bold]Mentioned files:[/bold]")
        for f in facts.mentioned_files:
            console.print(f"  • {f}")


def main() -> None:
    import sys
    if sys.version_info < (3, 11):
        print("Error: OpenForge requires Python 3.11+", file=sys.stderr)
        sys.exit(1)
    app()


if __name__ == "__main__":
    main()
