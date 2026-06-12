from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.repo_intel.index import diagnose_repo_intel_index, write_repo_intel_index
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.profiles import load_profiles
from xhx_agent.runtime.session import (
    format_follow_up,
    list_sessions,
    load_latest_session,
    load_session,
    load_transcript_messages,
    record_session,
)
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.textual_app import run_textual_console

app = typer.Typer(help="xhx-agent local coding agent CLI.")
config_app = typer.Typer(help="Manage xhx-agent configuration.")
app.add_typer(config_app, name="config")
console = Console()


@app.command("init")
def init() -> None:
    result = RuntimeApp().init_project()
    console.print("Initialized xhx-agent project.")
    console.print(f"config.json: {'created' if result.config_created else 'exists'}")
    console.print(f"profiles.json: {'created' if result.profiles_created else 'exists'}")
    console.print(f"XHX.md: {'created' if result.xhx_md_created else 'exists'}")
    console.print(f"repo index: {result.repo_index_path}")


@app.command("repo-index")
def repo_index(
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON diagnostics.")] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Rebuild .xhx/repo/index.json before printing diagnostics.")
    ] = False,
) -> None:
    if refresh:
        write_repo_intel_index(Path.cwd())
    diagnostics = diagnose_repo_intel_index(Path.cwd())
    if json_output:
        console.print(diagnostics.model_dump_json(indent=2))
        return
    console.print(f"repo index: {diagnostics.status}")
    console.print(f"path: {diagnostics.path}")
    console.print(f"reason: {diagnostics.reason}")
    if diagnostics.status in {"missing", "invalid"}:
        return
    console.print(f"schema: {diagnostics.schema_version}")
    console.print(f"size: {diagnostics.size_bytes} bytes")
    console.print(f"files: {diagnostics.file_count}")
    console.print(f"symbols: {diagnostics.symbol_count}")
    console.print(f"import edges: {diagnostics.import_edge_count}")
    console.print(f"call edges: {diagnostics.call_edge_count}")
    console.print(f"call graph truncated: {diagnostics.call_graph_truncated}")
    console.print(f"references: {diagnostics.reference_count}")
    console.print(f"reference truncated: {diagnostics.reference_truncated}")
    if diagnostics.skipped_reference_files:
        console.print("skipped reference files:")
        for path in diagnostics.skipped_reference_files[:10]:
            console.print(f"  - {path}")


@app.command("run")
def run(
    task: Annotated[str, typer.Argument(help="Task for xhx-agent to run.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON result.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Allow confirm-level verification commands.")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only build the first model plan; do not execute tools.")
    ] = False,
    auto_repair: Annotated[
        bool, typer.Option("--auto-repair", help="Allow up to two repair attempts after failed verification.")
    ] = False,
    cont: Annotated[
        bool,
        typer.Option("--continue", help="Resume from the most recent session, injecting its summary as context."),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume from a specific session by run id (see `xhx sessions`)."),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option("--mode", help="Orchestrator paradigm: loop | plan | graph (linear | dag = auto-classify fallback; default: auto-classified)."),
    ] = None,
) -> None:
    runtime = RuntimeApp()
    if dry_run:
        preview_result = runtime.preview_plan(task, profile)
        if json_output:
            console.print(preview_result.model_dump_json(indent=2))
            return
        console.print(f"status: {preview_result.status}")
        console.print(f"summary: {preview_result.summary}")
        console.print(f"steps: {preview_result.step_count}")
        console.print(
            f"context: {preview_result.context_used_tokens_estimate}/{preview_result.context_budget_tokens} estimated tokens"
        )
        console.print(f"trace: {preview_result.trace_path}")
        if preview_result.risk_summary:
            console.print("risks:")
            for risk in preview_result.risk_summary:
                console.print(f"  - {risk}")
        return
    effective_task = task
    prior_messages = None
    resume_mode = mode
    if cont or resume:
        previous = load_latest_session(runtime.workspace) if cont else load_session(runtime.workspace, resume or "")
        if previous is not None:
            restored = load_transcript_messages(runtime.workspace, previous.transcript_path)
            verb = "Continuing" if cont else "Resuming"
            if restored:
                prior_messages = restored
                resume_mode = mode or (previous.mode or None)
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — full transcript restored.")
            else:
                effective_task = format_follow_up(previous) + "\n\n" + task
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — summary only.")
        else:
            target = "most recent session" if cont else f"session '{resume}'"
            console.print(f"No {target} found; starting fresh.")
    if json_output:
        json_result = runtime.run_task(
            effective_task,
            profile,
            assume_yes=yes,
            auto_repair=auto_repair,
            mode=resume_mode,
            prior_messages=prior_messages,
        )
        record_session(runtime.workspace, task, json_result)
        console.print(json_result.model_dump_json(indent=2))
        return
    result = runtime.run_task(
        effective_task,
        profile,
        assume_yes=yes,
        confirm_callback=_confirm_terminal_command,
        auto_repair=auto_repair,
        mode=resume_mode,
        prior_messages=prior_messages,
    )
    record_session(runtime.workspace, task, result)
    console.print(f"status: {result.status}")
    console.print(f"summary: {result.summary_path}")
    if result.commands:
        console.print("verification commands:")
        for command in result.commands:
            console.print(f"  - {command}")
    if result.risk_summary:
        console.print("risks:")
        for risk in result.risk_summary:
            console.print(f"  - {risk}")


@app.command("sessions")
def sessions() -> None:
    from rich.table import Table

    runtime = RuntimeApp()
    entries = list_sessions(runtime.workspace)
    if not entries:
        console.print("No sessions recorded yet.")
        return
    table = Table(title="Sessions")
    table.add_column("run_id")
    table.add_column("status")
    table.add_column("verification")
    table.add_column("task")
    for entry in entries[-20:]:
        table.add_row(entry.run_id, entry.status, entry.verification, entry.task[:60])
    console.print(table)


@app.command("chat")
def chat() -> None:
    CommandConsole(console=console).run()


@app.command("tui")
def tui(
    fullscreen: Annotated[
        bool, typer.Option("--fullscreen", help="Run the experimental fullscreen Textual console.")
    ] = False,
) -> None:
    if fullscreen:
        run_textual_console()
        return
    CommandConsole(console=console).run()


@config_app.command("list")
def config_list() -> None:
    workspace = Path.cwd()
    config = load_config(workspace)
    profiles = load_profiles(workspace)
    console.print("config:")
    console.print(config.model_dump_json(indent=2))
    console.print("profiles:")
    console.print(profiles.model_dump_json(indent=2))


@config_app.command("set-profile")
def config_set_profile(profile: str) -> None:
    # v0.1 keeps config mutation out of scope; this command validates intent.
    console.print(f"Profile switching is planned. Requested profile: {profile}")


def _confirm_terminal_command(command: str, decision: PolicyDecision) -> bool:
    console.print("Verification command requires confirmation.")
    console.print(f"command: {command}")
    console.print(f"risk: {decision.risk.value}")
    console.print(f"reason: {decision.reason}")
    try:
        return typer.confirm("Run this command?", default=False, abort=False)
    except (typer.Abort, EOFError, KeyboardInterrupt):
        console.print("Verification command declined.")
        return False


@app.command("rpc")
def rpc() -> None:
    from xhx_agent.cli.rpc import start_rpc_loop

    start_rpc_loop()


@app.command("replay")
def replay(
    run_id: Annotated[str, typer.Argument(help="Run ID to replay from trace file.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON result.")] = False,
) -> None:
    from xhx_agent.evals.replay import TrailReplayer

    replayer = TrailReplayer(Path.cwd())
    try:
        result = replayer.replay(run_id)
        if json_output:
            console.print(result.model_dump_json(indent=2))
            return
        console.print(f"Replay successful for run: {run_id}")
        console.print(f"status: {result.status}")
        console.print(f"summary: {result.summary_path}")
        if result.metrics:
            console.print(f"turns: {result.metrics.turns}")
            console.print(f"duration: {result.metrics.duration_seconds}s")
            console.print(f"tokens: {result.metrics.tokens_estimate}")
    except Exception as e:
        console.print(f"[red]Error during replay: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("benchmark")
def benchmark(
    profile: Annotated[str, typer.Option("--profile", help="Model profile name to benchmark.")] = "mock",
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON results.")] = False,
    modes: Annotated[
        str | None,
        typer.Option("--modes", help="Comma-separated paradigms to compare, e.g. loop,plan,graph."),
    ] = None,
) -> None:
    from xhx_agent.evals.benchmark import BenchmarkRunner

    runner = BenchmarkRunner(Path.cwd())
    if not json_output:
        console.print(f"Running benchmark fixtures against profile: {profile}...")
    try:
        if modes:
            import json

            from xhx_agent.evals.benchmark import render_benchmark_report

            mode_list = [m.strip() for m in modes.split(",") if m.strip()]
            report = render_benchmark_report(profile, runner.run_matrix(profile, mode_list))
            if json_output:
                print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
                return
            out_dir = Path.cwd() / ".xhx" / "benchmark"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "report.md").write_text(report.markdown, encoding="utf-8")
            (out_dir / "report.json").write_text(
                json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            console.print(report.markdown)
            console.print("\n[green]Report written to .xhx/benchmark/report.md | report.json[/green]")
            return
        results = runner.run_benchmark(profile)
        if json_output:
            import json

            print(json.dumps([r.model_dump() for r in results], indent=2))
            return

        # Render a beautiful rich table
        from rich.table import Table

        table = Table(title=f"xhx-agent Benchmark Results ({profile})")
        table.add_column("Fixture ID", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Turns", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Est. Tokens", justify="right")
        table.add_column("Success", justify="center")

        for r in results:
            success_emoji = "✅" if r.success else "❌"
            status_style = "bold green" if r.status == "success" else "bold red"
            table.add_row(
                r.fixture_id,
                r.name,
                f"[{status_style}]{r.status}[/{status_style}]",
                str(r.turns),
                f"{r.duration_seconds:.2f}s",
                str(r.tokens_estimate),
                success_emoji,
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error running benchmarks: {e}[/red]")
        raise typer.Exit(code=1)
