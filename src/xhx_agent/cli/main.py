from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.profiles import load_profiles


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


@app.command("run")
def run(
    task: Annotated[str, typer.Argument(help="Task for xhx-agent to run.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON result.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Allow confirm-level verification commands.")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
) -> None:
    runtime = RuntimeApp()
    if json_output:
        console.print(runtime.run_task_json(task, profile, assume_yes=yes))
        return
    result = runtime.run_task(task, profile, assume_yes=yes)
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
    console.print("xhx-agent REPL. Type /exit to quit.")
    runtime = RuntimeApp()
    while True:
        text = typer.prompt("xhx")
        if text.strip() == "/exit":
            break
        result = runtime.run_task(text)
        console.print(f"status: {result.status}; summary: {result.summary_path}")


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
