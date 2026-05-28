from __future__ import annotations

from pathlib import Path
from typing import Annotated

import click
import typer
from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.repo_intel.index import diagnose_repo_intel_index
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.profiles import load_profiles
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
) -> None:
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
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Only build the first model plan; do not execute tools.")] = False,
    auto_repair: Annotated[bool, typer.Option("--auto-repair", help="Allow up to two repair attempts after failed verification.")] = False,
) -> None:
    runtime = RuntimeApp()
    if dry_run:
        result = runtime.preview_plan(task, profile)
        if json_output:
            console.print(result.model_dump_json(indent=2))
            return
        console.print(f"status: {result.status}")
        console.print(f"summary: {result.summary}")
        console.print(f"steps: {result.step_count}")
        console.print(f"context: {result.context_used_tokens_estimate}/{result.context_budget_tokens} estimated tokens")
        console.print(f"trace: {result.trace_path}")
        if result.risk_summary:
            console.print("risks:")
            for risk in result.risk_summary:
                console.print(f"  - {risk}")
        return
    if json_output:
        console.print(runtime.run_task_json(task, profile, assume_yes=yes, auto_repair=auto_repair))
        return
    result = runtime.run_task(task, profile, assume_yes=yes, confirm_callback=_confirm_terminal_command, auto_repair=auto_repair)
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


@app.command("chat")
def chat() -> None:
    CommandConsole(console=console).run()


@app.command("tui")
def tui(
    fullscreen: Annotated[bool, typer.Option("--fullscreen", help="Run the experimental fullscreen Textual console.")] = False,
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
    except (click.Abort, EOFError, KeyboardInterrupt):
        console.print("Verification command declined.")
        return False
