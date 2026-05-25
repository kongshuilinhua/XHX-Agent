from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from xhx_agent.runtime.app import RunResult, RuntimeApp
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.profiles import load_profiles
from xhx_agent.safety.policy import PolicyDecision


SLASH_COMMANDS = {
    "/help",
    "/model",
    "/status",
    "/plan",
    "/evidence",
    "/context",
    "/verify",
    "/repair",
    "/diff",
    "/skills",
    "/mode",
    "/clear",
    "/exit",
}


class CommandConsole:
    def __init__(self, workspace: Path | None = None, console: Console | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.console = console or Console()
        self.runtime = RuntimeApp(self.workspace)
        self.profile_name: str | None = None
        self.auto_repair = False
        self.assume_yes = False
        self.last_result: RunResult | None = None
        self.mode = "linear-edit"

    def run(self) -> None:
        self.console.print(Panel("xhx-agent command console. Type /help for commands, /exit to quit.", title="xhx-agent"))
        while True:
            try:
                text = typer.prompt("xhx")
            except (EOFError, KeyboardInterrupt):
                self.console.print("Exiting xhx-agent console.")
                return
            if not self.handle_input(text):
                return

    def handle_input(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self.handle_command(stripped)
        self.run_task(stripped)
        return True

    def handle_command(self, command_line: str) -> bool:
        command, _, argument = command_line.partition(" ")
        if command == "/exit":
            self.console.print("Exiting xhx-agent console.")
            return False
        if command == "/help":
            self.print_help()
        elif command == "/model":
            self.print_model(argument.strip() or None)
        elif command == "/status":
            self.print_status()
        elif command == "/plan":
            self.print_plan(argument.strip() or None)
        elif command == "/evidence":
            self.print_path_group("Evidence", ".xhx/evidence")
        elif command == "/context":
            self.print_path_group("Context", ".xhx/context")
        elif command == "/verify":
            self.print_verification()
        elif command == "/repair":
            self.toggle_repair(argument.strip())
        elif command == "/diff":
            self.print_changed_files()
        elif command == "/skills":
            self.print_path_group("Skills", ".xhx/skills")
        elif command == "/mode":
            self.set_mode(argument.strip())
        elif command == "/clear":
            self.console.clear()
        else:
            self.console.print(f"Unknown command: {command}. Type /help.")
        return True

    def run_task(self, task: str) -> None:
        self.console.print(Panel(task, title="Task"))
        result = self.runtime.run_task(
            task,
            profile_name=self.profile_name,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            auto_repair=self.auto_repair,
        )
        self.last_result = result
        self.print_run_result(result)

    def confirm_terminal_command(self, command: str, decision: PolicyDecision) -> bool:
        table = Table(title="Permission Required")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("command", command)
        table.add_row("risk", decision.risk.value)
        table.add_row("reason", decision.reason)
        self.console.print(table)
        return typer.confirm("Run this command?", default=False, abort=False)

    def print_help(self) -> None:
        table = Table(title="Slash Commands")
        table.add_column("Command")
        table.add_column("Behavior")
        rows = [
            ("/help", "Show commands."),
            ("/model [name]", "Show profiles or select a profile."),
            ("/status", "Show current console and last run status."),
            ("/plan [task]", "Preview a model plan without executing tools."),
            ("/evidence", "List Evidence Index files."),
            ("/context", "List context debug reports."),
            ("/verify", "Show last verification status."),
            ("/repair on|off", "Toggle auto repair."),
            ("/diff", "Show changed files from last run."),
            ("/skills", "List local skill directory entries."),
            ("/mode [name]", "Show or set console mode label."),
            ("/clear", "Clear terminal."),
            ("/exit", "Exit console."),
        ]
        for command, behavior in rows:
            table.add_row(command, behavior)
        self.console.print(table)

    def print_model(self, profile_name: str | None = None) -> None:
        profiles = load_profiles(self.workspace)
        if profile_name:
            self.profile_name = profile_name
            self.console.print(f"Active profile: {self.profile_name}")
            return
        table = Table(title="Model Profiles")
        table.add_column("Name")
        table.add_column("Provider")
        table.add_column("Model")
        for profile in profiles.profiles:
            marker = " *" if profile.name == (self.profile_name or load_config(self.workspace).default_profile) else ""
            table.add_row(profile.name + marker, profile.provider, profile.model or "")
        self.console.print(table)

    def print_status(self) -> None:
        table = Table(title="Console Status")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("workspace", str(self.workspace))
        table.add_row("profile", self.profile_name or load_config(self.workspace).default_profile)
        table.add_row("mode", self.mode)
        table.add_row("auto_repair", str(self.auto_repair).lower())
        table.add_row("assume_yes", str(self.assume_yes).lower())
        if self.last_result:
            table.add_row("last_status", self.last_result.status)
            table.add_row("last_summary", self.last_result.summary_path)
        self.console.print(table)

    def print_plan(self, task: str | None = None) -> None:
        if not task:
            self.console.print("Usage: /plan <task>")
            return
        result = self.runtime.preview_plan(task, self.profile_name)
        table = Table(title="Plan Preview")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("status", result.status)
        table.add_row("summary", result.summary)
        table.add_row("steps", str(result.step_count))
        table.add_row("context", f"{result.context_used_tokens_estimate}/{result.context_budget_tokens}")
        table.add_row("trace", result.trace_path)
        self.console.print(table)

    def print_path_group(self, title: str, relative_dir: str) -> None:
        directory = self.workspace / relative_dir
        table = Table(title=title)
        table.add_column("Path")
        if directory.exists():
            for path in sorted(directory.glob("*")):
                table.add_row(str(path.relative_to(self.workspace)))
        if not directory.exists() or not list(directory.glob("*")):
            table.add_row("none")
        self.console.print(table)

    def print_verification(self) -> None:
        if not self.last_result:
            self.console.print("No task has run in this console.")
            return
        table = Table(title="Verification")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("status", self.last_result.verification)
        table.add_row("commands", ", ".join(self.last_result.commands) or "none")
        table.add_row("repair_attempts", str(self.last_result.repair_attempts))
        self.console.print(table)

    def toggle_repair(self, argument: str) -> None:
        if argument.lower() in {"on", "true", "1"}:
            self.auto_repair = True
        elif argument.lower() in {"off", "false", "0"}:
            self.auto_repair = False
        self.console.print(f"auto_repair: {str(self.auto_repair).lower()}")

    def print_changed_files(self) -> None:
        if not self.last_result:
            self.console.print("No task has run in this console.")
            return
        self.console.print(Panel("\n".join(self.last_result.changed_files) or "none", title="Changed Files"))

    def set_mode(self, argument: str) -> None:
        if argument:
            self.mode = argument
        self.console.print(f"mode: {self.mode}")

    def print_run_result(self, result: RunResult) -> None:
        table = Table(title="Run Result")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("status", result.status)
        table.add_row("verification", result.verification)
        table.add_row("changed_files", ", ".join(result.changed_files) or "none")
        table.add_row("commands", ", ".join(result.commands) or "none")
        table.add_row("repair_attempts", str(result.repair_attempts))
        table.add_row("summary", result.summary_path)
        if result.restore_plan_path:
            table.add_row("restore_plan", result.restore_plan_path)
        self.console.print(table)
        if result.risk_summary:
            self.console.print(Panel("\n".join(result.risk_summary), title="Risks"))
