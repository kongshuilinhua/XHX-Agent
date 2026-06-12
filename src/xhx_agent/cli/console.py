from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import typer
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from xhx_agent.cli.completion import XhxCompleter
from xhx_agent.memory.store import MemoryRecord
from xhx_agent.runtime.app import DiffSummary, ManualRepairResult, ManualVerificationResult, RunResult, RuntimeApp
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.runtime.profiles import load_profiles
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.live import LiveDashboard
from xhx_agent.tui.page import render_console_page
from xhx_agent.tui.state import ConsoleState


class PromptToolkitCompleter(Completer):
    def __init__(self, completer: XhxCompleter) -> None:
        self.completer = completer

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.strip():
            return
        candidates = self.completer.get_completions(text)
        for val in candidates:
            yield Completion(val, start_position=-len(text))


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
    "/dashboard",
    "/live",
    "/cancel",
    "/clear",
    "/exit",
    "/remember",
    "/memory",
}


class CommandConsole:
    def __init__(
        self,
        workspace: Path | None = None,
        console: Console | None = None,
        live_enabled: bool | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.console = console or Console()
        self.runtime = RuntimeApp(self.workspace)
        self.profile_name: str | None = None
        self.auto_repair = False
        self.assume_yes = False
        self.auto_memory = True
        self.last_result: RunResult | None = None
        self.last_manual_verification: ManualVerificationResult | None = None
        self.last_manual_repair: ManualRepairResult | None = None
        self.last_user_task: str | None = None
        self.last_runtime_task: str | None = None
        self.events: list[RuntimeEvent] = []
        self.state = ConsoleState()
        self.mode = "linear-edit"
        self.state.mode = self.mode
        self.cancel_requested = False
        self.live_enabled = self.console.is_interactive if live_enabled is None else live_enabled
        self.live_dashboard: LiveDashboard | None = None
        self.completer = XhxCompleter(self.workspace)
        self.prompt_session: PromptSession | None = None
        try:
            self.prompt_session = PromptSession(completer=PromptToolkitCompleter(self.completer))
        except Exception:
            pass

    def run(self) -> None:
        self.console.print(
            Panel("xhx-agent command console. Type /help for commands, /exit to quit.", title="xhx-agent")
        )
        self.print_dashboard()
        while True:
            try:
                text = self.prompt_session.prompt("xhx> ") if self.prompt_session else typer.prompt("xhx")
            except EOFError:
                self.console.print("Exiting xhx-agent console.")
                return
            except KeyboardInterrupt:
                if self.request_cancel("Keyboard interrupt requested cancellation."):
                    continue
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
            self.print_evidence()
        elif command == "/context":
            self.print_context()
        elif command == "/verify":
            self.run_manual_verification()
        elif command == "/repair":
            self.toggle_repair(argument.strip())
        elif command == "/diff":
            self.print_changed_files()
        elif command == "/skills":
            self.print_path_group("Skills", ".xhx/skills")
        elif command == "/mode":
            self.set_mode(argument.strip())
        elif command == "/dashboard":
            self.print_dashboard()
        elif command == "/live":
            self.set_live(argument.strip())
        elif command == "/cancel":
            self.request_cancel()
        elif command == "/clear":
            self.console.clear()
            self.print_dashboard()
        elif command == "/remember":
            self.handle_remember(argument.strip())
        elif command == "/memory":
            self.handle_memory()
        else:
            self.console.print(f"Unknown command: {command}. Type /help.")
        return True

    @property
    def orchestrator_mode(self) -> str | None:
        """Explicit orchestrator paradigm to use, or None to auto-classify.

        ``/mode loop|plan|graph`` (or the ``linear|dag`` fallback) selects a paradigm; any other label (the
        default ``linear-edit``) means auto-classification, preserving behaviour.
        """
        return self.mode if self.mode in {"plan", "loop", "graph", "linear", "dag"} else None

    def run_task(self, task: str) -> None:
        self.cancel_requested = False
        runtime_task = self.build_runtime_task(task)
        self.last_user_task = task
        self.last_runtime_task = runtime_task
        self.console.print(Panel(task, title="Task"))
        if runtime_task != task:
            self.console.print(Panel(runtime_task, title="Follow-up Context"))
        try:
            with self.cancel_signal_handler():
                if self.live_enabled:
                    with self.open_live_dashboard():
                        result = self.runtime.run_task(
                            runtime_task,
                            profile_name=self.profile_name,
                            assume_yes=self.assume_yes,
                            confirm_callback=self.confirm_terminal_command,
                            auto_repair=self.auto_repair,
                            event_callback=self.handle_event,
                            cancel_check=self.is_cancel_requested,
                            mode=self.orchestrator_mode,
                        )
                else:
                    self.print_dashboard()
                    result = self.runtime.run_task(
                        runtime_task,
                        profile_name=self.profile_name,
                        assume_yes=self.assume_yes,
                        confirm_callback=self.confirm_terminal_command,
                        auto_repair=self.auto_repair,
                        event_callback=self.handle_event,
                        cancel_check=self.is_cancel_requested,
                        mode=self.orchestrator_mode,
                    )
        except KeyboardInterrupt:
            self.request_cancel("Keyboard interrupt requested cancellation.", force=True)
            self.console.print("Task interrupted before the runtime could finish cancellation cleanup.")
            return
        self.last_result = result
        self.state.apply_result(result)
        self.print_run_result(result)
        self._maybe_suggest_memories(result)
        self.print_dashboard()

    @contextmanager
    def open_live_dashboard(self) -> Iterator[LiveDashboard]:
        dashboard = LiveDashboard(
            self.console,
            self.state,
            workspace=str(self.workspace),
            profile=self.active_profile_name(),
            auto_repair=self.auto_repair,
            assume_yes=self.assume_yes,
        )
        self.live_dashboard = dashboard
        try:
            with dashboard:
                yield dashboard
        finally:
            self.live_dashboard = None

    @contextmanager
    def cancel_signal_handler(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            yield
            return
        previous_handler = signal.getsignal(signal.SIGINT)

        def handle_sigint(_signum, _frame) -> None:
            self.request_cancel("Keyboard interrupt requested cancellation.", force=True)

        signal.signal(signal.SIGINT, handle_sigint)
        try:
            yield
        finally:
            signal.signal(signal.SIGINT, previous_handler)

    def build_runtime_task(self, task: str) -> str:
        if self.last_result is None:
            return task
        return "\n".join(
            [
                "Follow-up task in the same console session.",
                "",
                "User request:",
                task,
                "",
                "Previous run context:",
                f"- run_id: {self.last_result.run_id}",
                f"- status: {self.last_result.status}",
                f"- verification: {self.last_result.verification}",
                f"- changed_files: {', '.join(self.last_result.changed_files) or 'none'}",
                f"- summary: {self.last_result.summary_path}",
                "",
                "Use the previous run context only when it is relevant. Keep normal safety, apply_patch, and verification rules.",
            ]
        )

    def handle_event(self, event: RuntimeEvent) -> None:
        self.events.append(event)
        self.state.reduce(event)
        self.refresh_live_dashboard()
        if event.type == "model_delta":
            if not self.live_enabled:
                self.console.print(event.message, end="")
            return
        if self.live_enabled:
            return
        self.console.print(f"[dim]{event.type}[/dim] {event.message}")
        if event.type in {"tool_result", "verification_result", "run_end"} and event.payload:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column("Field")
            table.add_column("Value")
            for key, value in event.payload.items():
                table.add_row(str(key), str(value))
            self.console.print(table)

    def request_cancel(self, reason: str = "Cancel requested by user.", force: bool = False) -> bool:
        if not force and self.state.status in {"idle", "success", "failed", "cancelled", "skipped_no_changes"}:
            self.console.print("No running task to cancel.")
            return False
        self.cancel_requested = True
        event = RuntimeEvent(type="cancel_requested", message=reason, payload={"source": "console"})
        self.handle_event(event)
        self.refresh_live_dashboard()
        self.console.print("Cancel requested. The current task will stop at the next safe runtime boundary.")
        return True

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested

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
            ("/verify", "Run verification for current changed files."),
            ("/repair [run|loop|on|off]", "Run manual repair, repair loop, or toggle auto repair."),
            ("/diff", "Show changed files and a read-only git diff summary."),
            ("/skills", "List local skill directory entries."),
            ("/mode [name]", "Show or set console mode label."),
            ("/dashboard", "Render the console dashboard."),
            ("/live [on|off]", "Toggle Rich Live dashboard refresh."),
            ("/cancel", "Request cancellation at the next safe runtime boundary."),
            ("/clear", "Clear terminal."),
            ("/remember <text>", "Remember a fact across sessions."),
            ("/memory", "List remembered facts."),
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
        self.console.print(self.status_table())

    def status_table(self) -> Table:
        table = Table(title="Console Status")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("workspace", str(self.workspace))
        table.add_row("profile", self.profile_name or load_config(self.workspace).default_profile)
        table.add_row("mode", self.mode)
        table.add_row("state", self.state.status)
        table.add_row("run_id", self.state.run_id or "none")
        table.add_row("cancel_requested", str(self.cancel_requested).lower())
        table.add_row("live", str(self.live_enabled).lower())
        table.add_row("auto_repair", str(self.auto_repair).lower())
        table.add_row("assume_yes", str(self.assume_yes).lower())
        table.add_row("events", str(len(self.events)))
        table.add_row("changed_files", str(len(self.state.changed_files)))
        table.add_row("verification", self.state.verification)
        if self.last_result:
            table.add_row("last_status", self.last_result.status)
            table.add_row("last_summary", self.last_result.summary_path)
        return table

    def print_plan(self, task: str | None = None) -> None:
        if not task:
            self.console.print(self.plan_table())
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

    def plan_table(self) -> Table:
        table = Table(title="Current Plan")
        table.add_column("Field")
        table.add_column("Value")
        if not self.state.plan_summary:
            table.add_row("status", "No active plan.")
            return table
        table.add_row("summary", self.state.plan_summary)
        table.add_row("status", self.state.plan_status or "unknown")
        table.add_row("steps", str(self.state.plan_step_count))
        return table

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

    def print_evidence(self) -> None:
        table = Table(title="Evidence Summary")
        table.add_column("Kind")
        table.add_column("Source")
        table.add_column("Decision")
        if self.state.policy_decisions:
            for item in self.state.policy_decisions[-6:]:
                table.add_row(item.scope or "policy", item.source or "unknown", f"{item.decision}: {item.reason}")
        else:
            table.add_row("none", "none", "No policy evidence in current console state.")
        self.console.print(table)
        self.print_path_group("Evidence Files", ".xhx/evidence")

    def print_context(self) -> None:
        table = Table(title="Context Summary")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("turn", str(self.state.context_turn or "none"))
        table.add_row("selected", str(self.state.context_selected))
        table.add_row("omitted", str(self.state.context_omitted))
        if self.state.context_budget_tokens:
            table.add_row(
                "budget",
                f"{self.state.context_used_tokens_estimate}/{self.state.context_budget_tokens} estimated tokens",
            )
        else:
            table.add_row("budget", "none")
        table.add_row("languages", ", ".join(self.state.detected_languages) or "unknown")
        table.add_row("files", str(self.state.file_count))
        self.console.print(table)
        self.print_path_group("Context Reports", ".xhx/context")

    def print_verification(self) -> None:
        if not self.last_result and not self.last_manual_verification:
            self.console.print("No verification has run in this console.")
            return
        table = Table(title="Verification")
        table.add_column("Field")
        table.add_column("Value")
        if self.last_manual_verification:
            table.add_row("manual_status", self.last_manual_verification.status)
            table.add_row("manual_commands", ", ".join(self.last_manual_verification.commands) or "none")
            table.add_row("manual_summary", self.last_manual_verification.summary_path or "none")
        if self.last_result:
            table.add_row("last_status", self.last_result.verification)
            table.add_row("last_commands", ", ".join(self.last_result.commands) or "none")
            table.add_row("repair_attempts", str(self.last_result.repair_attempts))
        for index, item in enumerate(self.state.verifications[-5:], start=1):
            exit_code = "none" if item.exit_code is None else str(item.exit_code)
            table.add_row(f"event_{index}", f"{item.command}: {item.status}, exit_code={exit_code}")
        self.console.print(table)

    def run_manual_verification(self) -> None:
        changed_files = list(self.state.changed_files)
        if not changed_files and self.last_result:
            changed_files = list(self.last_result.changed_files)
        result = self.runtime.verify_changed_files(
            changed_files,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            event_callback=self.handle_event,
            cancel_check=self.is_cancel_requested,
        )
        self.last_manual_verification = result
        self.print_manual_verification_result(result)
        self.print_dashboard()

    def print_manual_verification_result(self, result: ManualVerificationResult) -> None:
        table = Table(title="Manual Verification Result")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("status", result.status)
        table.add_row("changed_files", ", ".join(result.changed_files) or "none")
        table.add_row("commands", ", ".join(result.commands) or "none")
        table.add_row("summary", result.summary_path or "none")
        self.console.print(table)
        if result.risk_summary:
            self.console.print(Panel("\n".join(result.risk_summary), title="Verification Risks"))

    def toggle_repair(self, argument: str) -> None:
        if argument.lower() in {"", "run"}:
            self.run_manual_repair()
            return
        if argument.lower() in {"loop", "auto"}:
            self.run_manual_repair(max_attempts=2)
            return
        if argument.lower() in {"on", "true", "1"}:
            self.auto_repair = True
        elif argument.lower() in {"off", "false", "0"}:
            self.auto_repair = False
        self.console.print(f"auto_repair: {str(self.auto_repair).lower()}")

    def run_manual_repair(self, max_attempts: int = 1) -> None:
        failed_results = []
        changed_files: list[str] = []
        task = self.state.task or "manual repair"
        if self.last_manual_verification and self.last_manual_verification.status == "failed":
            failed_results = self.last_manual_verification.verification_results
            changed_files = list(self.last_manual_verification.changed_files)
        elif self.last_result and self.last_result.verification == "failed":
            failed_results = self.last_result.verification_results
            changed_files = list(self.last_result.changed_files)
            task = self.last_result.run_id
        else:
            self.console.print("Manual repair requires a failed verification result.")
            return
        result = self.runtime.repair_after_failed_verification(
            task=task,
            changed_files=changed_files,
            failed_verification_results=failed_results,
            profile_name=self.profile_name,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            event_callback=self.handle_event,
            cancel_check=self.is_cancel_requested,
            max_attempts=max_attempts,
        )
        self.last_manual_repair = result
        self.print_manual_repair_result(result)
        self.print_dashboard()

    def print_manual_repair_result(self, result: ManualRepairResult) -> None:
        table = Table(title="Manual Repair Result")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("status", result.status)
        table.add_row("verification", result.verification)
        table.add_row("changed_files", ", ".join(result.changed_files) or "none")
        table.add_row("commands", ", ".join(result.commands) or "none")
        table.add_row("repair_attempts", str(result.repair_attempts))
        table.add_row("summary", result.summary_path or "none")
        if result.restore_plan_path:
            table.add_row("restore_plan", result.restore_plan_path)
        self.console.print(table)
        if result.risk_summary:
            self.console.print(Panel("\n".join(result.risk_summary), title="Repair Risks"))

    def print_changed_files(self) -> None:
        changed_files = self.current_changed_files()
        if not changed_files:
            if not self.last_result:
                self.console.print("No task has run in this console.")
            else:
                self.console.print("No changed files in the current console state.")
            return
        self.print_diff_summary(self.runtime.diff_changed_files(changed_files))

    def current_changed_files(self) -> list[str]:
        if self.state.changed_files:
            return list(self.state.changed_files)
        if self.last_manual_repair and self.last_manual_repair.changed_files:
            return list(self.last_manual_repair.changed_files)
        if self.last_manual_verification and self.last_manual_verification.changed_files:
            return list(self.last_manual_verification.changed_files)
        if self.last_result and self.last_result.changed_files:
            return list(self.last_result.changed_files)
        return []

    def print_diff_summary(self, result: DiffSummary) -> None:
        table = Table(title="Diff Summary")
        table.add_column("Field")
        table.add_column("Value")
        table.add_row("summary", result.summary)
        table.add_row("changed_files", "\n".join(result.changed_files) or "none")
        table.add_row("truncated", str(result.truncated).lower())
        self.console.print(table)
        if result.diff_text:
            self.console.print(Panel(result.diff_text, title="Git Diff"))
        if result.risk_summary:
            self.console.print(Panel("\n".join(result.risk_summary), title="Diff Notes"))

    def set_mode(self, argument: str) -> None:
        if argument:
            self.mode = argument
            self.state.mode = argument
        self.console.print(f"mode: {self.mode}")

    def set_live(self, argument: str) -> None:
        lowered = argument.lower()
        if lowered in {"on", "true", "1"}:
            self.live_enabled = True
        elif lowered in {"off", "false", "0"}:
            self.live_enabled = False
        self.console.print(f"live: {str(self.live_enabled).lower()}")

    def active_profile_name(self) -> str:
        return self.profile_name or load_config(self.workspace).default_profile

    def refresh_live_dashboard(self) -> None:
        if self.live_dashboard is None:
            return
        self.live_dashboard.update_options(
            profile=self.active_profile_name(),
            auto_repair=self.auto_repair,
            assume_yes=self.assume_yes,
        )
        self.live_dashboard.refresh()

    def print_dashboard(self) -> None:
        self.console.print(
            render_console_page(
                self.state,
                workspace=str(self.workspace),
                profile=self.active_profile_name(),
                auto_repair=self.auto_repair,
                assume_yes=self.assume_yes,
            )
        )

    def last_run_table(self) -> Table:
        table = Table(title="Last Run")
        table.add_column("Field")
        table.add_column("Value")
        if not self.last_result:
            table.add_row("status", "none")
            return table
        table.add_row("status", self.last_result.status)
        table.add_row("verification", self.last_result.verification)
        table.add_row("changed", str(len(self.last_result.changed_files)))
        table.add_row("summary", self.last_result.summary_path)
        return table

    def activity_table(self) -> Table:
        table = Table(title="Activity")
        table.add_column("Type")
        table.add_column("Status")
        table.add_column("Summary")
        if self.state.tools:
            for item in self.state.tools[-3:]:
                table.add_row(f"tool:{item.tool}", item.status, item.summary or "")
        if self.state.verifications:
            for vitem in self.state.verifications[-2:]:
                exit_code = "none" if vitem.exit_code is None else str(vitem.exit_code)
                table.add_row("verify", vitem.status, f"{vitem.command} exit_code={exit_code}")
        if self.state.repair_attempts:
            table.add_row(
                "repair",
                f"{self.state.repair_attempts}/{self.state.repair_max_attempts or '?'}",
                self.state.repair_reason,
            )
        if not self.state.tools and not self.state.verifications and not self.state.repair_attempts:
            table.add_row("none", "idle", "No activity yet.")
        return table

    def event_table(self) -> Table:
        table = Table(title="Recent Events")
        table.add_column("Type")
        table.add_column("Message")
        for event in self.events[-5:]:
            table.add_row(event.type, event.message)
        if not self.events:
            table.add_row("none", "No events yet.")
        return table

    def command_table(self) -> Table:
        table = Table(title="Commands")
        table.add_column("Command")
        for command in sorted(SLASH_COMMANDS):
            table.add_row(command)
        return table

    def print_run_result(self, result: RunResult) -> None:
        if result.answer:
            self.console.print(Panel(result.answer, title="Answer", border_style="green"))
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

    def handle_remember(self, text: str) -> None:
        text_stripped = text.strip()
        if not text_stripped:
            self.console.print("[red]Please specify the memory text: /remember <text>[/red]")
            return
        first_line = text_stripped.splitlines()[0]
        name = first_line[:30].strip()
        import re
        parts = re.split(r'[。\.!\?？！\n]', text_stripped)
        first_sentence = parts[0].strip() if parts else first_line
        if len(first_sentence) > 80:
            first_sentence = first_sentence[:77] + "..."
        description = first_sentence or first_line[:80]

        from xhx_agent.memory import write_memory
        try:
            write_memory(self.workspace, name=name, description=description, mtype="project", body=text_stripped)
            self.console.print(f"[green]Remembered: [bold]{name}[/bold] — {description}[/green]")
        except Exception as e:
            self.console.print(f"[red]Failed to write memory: {e}[/red]")

    def handle_memory(self) -> None:
        from xhx_agent.memory import list_memories
        memories = list_memories(self.workspace)
        if not memories:
            self.console.print("No memories recorded yet.")
            return
        table = Table(title="Memories")
        table.add_column("Name")
        table.add_column("Type")
        table.add_column("Description")
        for m in memories:
            table.add_row(m.name, m.mtype, m.description)
        self.console.print(table)

    def _maybe_suggest_memories(self, result: RunResult) -> None:
        """跑完（成功）后自动抽取候选记忆并逐条请用户确认（suggest-confirm）。

        增益功能：任何环节失败都**静默跳过**，绝不影响任务结果或既有输出；只在真正写入时才打印。
        mock profile 下抽取确定性返回空 → 无任何提示。用 ``/automem off`` 关闭。
        """
        if not self.auto_memory or result.status != "success":
            return
        try:
            from xhx_agent.memory import list_memories, propose_memories, write_memory
            from xhx_agent.memory.store import slugify
            from xhx_agent.models import build_chat_client
            from xhx_agent.runtime.config import load_config
            from xhx_agent.runtime.profiles import get_profile

            task = self.last_user_task or self.last_runtime_task or ""
            answer = (getattr(result, "answer", None) or "").strip()
            digest = f"Assistant: {answer}" if answer else f"Run status: {result.status}"
            config = load_config(self.workspace)
            profile = get_profile(self.workspace, self.profile_name or config.default_profile)
            client = build_chat_client(profile)
            existing = {slugify(record.name) for record in list_memories(self.workspace)}
            candidates = propose_memories(client, task, digest, existing_names=existing)
            for candidate in candidates:
                if self._confirm_memory(candidate):
                    write_memory(
                        self.workspace,
                        name=candidate.name,
                        description=candidate.description,
                        mtype=candidate.mtype,
                        body=candidate.body,
                    )
                    self.console.print(f"[green]Remembered: [bold]{candidate.name}[/bold][/green]")
        except Exception:
            # 自动记忆是 best-effort 增益，绝不打断主流程。
            return

    def _confirm_memory(self, record: MemoryRecord) -> bool:
        """展示候选记忆并询问是否长期记住（默认否）。非交互/异常一律视为否。"""
        self.console.print(
            Panel(
                f"[{record.mtype}] {record.description}\n{record.body}".strip(),
                title="Remember this across sessions? (suggested)",
            )
        )
        try:
            return bool(typer.confirm("Save to long-term memory?", default=False))
        except Exception:
            return False

