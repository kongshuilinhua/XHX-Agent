from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static

from xhx_agent.runtime.app import ManualRepairResult, ManualVerificationResult, RunResult, RuntimeApp
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.runtime.profiles import load_profiles
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.page import SLASH_COMMAND_HINTS
from xhx_agent.tui.state import ConsoleState


T = TypeVar("T")


@dataclass(frozen=True)
class TextualSnapshot:
    header: str
    conversation: str
    runtime_state: str
    changed_files: str
    commands: str

    @classmethod
    def from_state(
        cls,
        state: ConsoleState,
        *,
        workspace: str,
        profile: str,
        auto_repair: bool,
        assume_yes: bool,
        pending_steer: str | None = None,
        next_confirm_response: bool | None = None,
    ) -> TextualSnapshot:
        run_id = state.run_id or "none"
        header = f"xhx-agent | {state.status} | profile: {profile} | run: {run_id}"
        flags = []
        if auto_repair:
            flags.append("repair:on")
        if assume_yes:
            flags.append("yes:on")
        conversation_lines = []
        if state.task:
            conversation_lines.append(f"user> {state.task}")
        if state.plan_summary:
            conversation_lines.append(f"plan> {state.plan_summary}")
        if state.model_output:
            conversation_lines.append(f"model> {' '.join(state.model_output.split())}")
        if state.summary_path:
            conversation_lines.append(f"summary> {state.summary_path}")
        if state.cancel_requested:
            conversation_lines.append(f"cancel> {state.cancel_reason or 'requested'}")
        if getattr(state, "textual_messages", None):
            conversation_lines.extend(str(item) for item in getattr(state, "textual_messages"))
        if not conversation_lines:
            conversation_lines.append("No conversation yet.")
        permission_state = "next confirm: default-deny"
        if next_confirm_response is True:
            permission_state = "next confirm: allow once"
        elif next_confirm_response is False:
            permission_state = "next confirm: deny once"
        pending_policy = next(
            (item for item in reversed(state.policy_decisions) if item.requires_user or item.decision == "confirm"),
            None,
        )
        if pending_policy is not None:
            permission_state += f"\nwaiting: {pending_policy.source or pending_policy.scope} ({pending_policy.risk})"
        active_tool = next((item for item in reversed(state.tools) if item.status == "running"), None)
        active_verification = next((item for item in reversed(state.verifications) if item.status == "running"), None)
        runtime_state = "\n".join(
            [
                f"workspace: {workspace}",
                f"mode: {state.mode}",
                f"pending steer: {pending_steer or 'none'}",
                f"cancel: {'requested' if state.cancel_requested else 'none'}",
                permission_state,
                f"active tool: {active_tool.tool if active_tool else 'none'}",
                f"active verification: {active_verification.command if active_verification else 'none'}",
                f"verification: {state.verification}",
                f"repair: {state.repair_attempts}/{state.repair_max_attempts or 0}",
                f"context: {state.context_used_tokens_estimate}/{state.context_budget_tokens or 0}",
                f"events: {len(state.events)}",
                f"flags: {', '.join(flags) or 'none'}",
            ]
        )
        changed_files = "\n".join(state.changed_files) if state.changed_files else "none"
        commands = " ".join(SLASH_COMMAND_HINTS)
        return cls(
            header=header,
            conversation="\n".join(conversation_lines),
            runtime_state=runtime_state,
            changed_files=changed_files,
            commands=commands,
        )


class TextualCommandConsoleApp(App[None]):
    """Fullscreen v0.5 shell that renders ConsoleState without owning Runtime internals."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #body {
        height: 1fr;
    }

    #conversation {
        width: 2fr;
        border: solid $primary;
        padding: 1;
    }

    #side {
        width: 1fr;
        border: solid $secondary;
        padding: 1;
    }

    #input {
        height: 3;
    }
    """

    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+l", "clear", "Clear"),
    ]

    def __init__(
        self,
        *,
        workspace: Path | None = None,
        profile: str = "mock",
        auto_repair: bool = False,
        assume_yes: bool = False,
        state: ConsoleState | None = None,
        runtime: RuntimeApp | None = None,
    ) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        self.runtime = runtime or RuntimeApp(self.workspace)
        self.profile = profile
        self.auto_repair = auto_repair
        self.assume_yes = assume_yes
        self.state = state or ConsoleState()
        self.last_result: RunResult | None = None
        self.last_manual_verification: ManualVerificationResult | None = None
        self.last_manual_repair: ManualRepairResult | None = None
        self.next_confirm_response: bool | None = None
        self.messages: list[str] = []
        self.exit_requested = False
        self.cancel_requested = False
        self.pending_steer: str | None = None
        self.widgets_ready = False
        self.ui_thread_id: int | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield Static(id="conversation")
            with Vertical(id="side"):
                yield Static(id="runtime")
                yield Static(id="changed")
                yield Static(id="commands")
        yield Input(placeholder="Type a task or slash command. Fullscreen execution wiring is v0.5 in progress.", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self.ui_thread_id = threading.get_ident()
        self.widgets_ready = True
        self.refresh_snapshot()

    def refresh_snapshot(self) -> None:
        self.state.textual_messages = list(self.messages)  # type: ignore[attr-defined]
        snapshot = TextualSnapshot.from_state(
            self.state,
            workspace=str(self.workspace),
            profile=self.profile,
            auto_repair=self.auto_repair,
            assume_yes=self.assume_yes,
            pending_steer=self.pending_steer,
            next_confirm_response=self.next_confirm_response,
        )
        self.title = snapshot.header
        if not self.widgets_ready:
            return
        self.query_one("#conversation", Static).update(snapshot.conversation)
        self.query_one("#runtime", Static).update(snapshot.runtime_state)
        self.query_one("#changed", Static).update("changed files:\n" + snapshot.changed_files)
        self.query_one("#commands", Static).update(snapshot.commands)

    def action_clear(self) -> None:
        self.state = ConsoleState()
        self.messages.clear()
        self.refresh_snapshot()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.input.value = ""
        should_continue = self.handle_text_input(event.value, use_worker=True)
        self.refresh_snapshot()
        if not should_continue:
            self.exit()

    def handle_text_input(self, text: str, *, use_worker: bool = False) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self.handle_slash_command(stripped)
        if self.is_running():
            self.queue_steer(stripped)
            return True
        if use_worker and self.widgets_ready:
            self.start_task_worker(stripped)
        else:
            self.run_task(stripped)
        return True

    def start_task_worker(self, task: str) -> None:
        self.cancel_requested = False
        self.state.status = "running"
        self.state.task = task
        self.append_message(f"user> {task}")
        self.refresh_snapshot()
        self.run_worker(
            lambda: self.run_task(task, announce_user=False, reset_cancel=False),
            name="runtime-task",
            group="runtime",
            thread=True,
        )

    def run_task(self, task: str, *, announce_user: bool = True, reset_cancel: bool = True) -> None:
        if reset_cancel:
            self.cancel_requested = False
        if announce_user:
            self.append_message(f"user> {task}")
        runtime_task = self.build_runtime_task(task)
        if runtime_task != task:
            self.append_message("system> follow-up context attached")
        result = self.runtime.run_task(
            runtime_task,
            profile_name=self.profile,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            auto_repair=self.auto_repair,
            event_callback=self.handle_runtime_event,
            cancel_check=self.is_cancel_requested,
        )
        self.last_result = result
        self.apply_run_result(result)
        self.append_message(f"system> run finished: {result.status}, verification: {result.verification}")
        self.run_pending_steer()

    def run_pending_steer(self) -> None:
        if self.pending_steer is None:
            return
        steer = self.pending_steer
        self.pending_steer = None
        self.append_message("running queued steer as follow-up")
        self.run_task(steer)

    def queue_steer(self, text: str) -> None:
        self.pending_steer = text
        self.append_message(f"system> steer queued: {text}")
        self.request_cancel("Steer requested by user.")

    def is_running(self) -> bool:
        return self.state.status not in {"idle", "success", "failed", "cancelled", "skipped_no_changes"}

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

    def handle_runtime_event(self, event: RuntimeEvent) -> None:
        self.call_ui(self.apply_runtime_event, event)

    def confirm_terminal_command(self, command: str, decision: PolicyDecision) -> bool:
        allowed = bool(self.next_confirm_response)
        self.next_confirm_response = None
        verb = "allowed" if allowed else "declined"
        self.append_message(f"system> permission {verb}: {command} ({decision.risk.value})")
        return allowed

    def handle_slash_command(self, command_line: str) -> bool:
        command, _, argument = command_line.partition(" ")
        argument = argument.strip()
        if command == "/exit":
            self.exit_requested = True
            return False
        if command == "/clear":
            self.action_clear()
            return True
        if command == "/allow":
            self.next_confirm_response = True
            self.append_message("system> next permission prompt will be allowed once")
            return True
        if command == "/deny":
            self.next_confirm_response = False
            self.append_message("system> next permission prompt will be declined once")
            return True
        if command == "/help":
            self.append_message(
                "system> available commands: /help /model /status /plan /context /evidence /diff /verify /repair /skills /mode /dashboard /cancel /live /allow /deny /clear /exit"
            )
            return True
        if command == "/model":
            self.print_model(argument or None)
            return True
        if command == "/plan":
            self.print_plan_preview(argument or None)
            return True
        if command == "/mode":
            self.set_mode(argument)
            return True
        if command == "/context":
            self.print_context_summary()
            return True
        if command == "/evidence":
            self.print_evidence_summary()
            return True
        if command == "/diff":
            self.print_diff_summary()
            return True
        if command == "/verify":
            self.run_manual_verification()
            return True
        if command == "/repair":
            max_attempts = 2 if argument.lower() in {"loop", "auto"} else 1
            self.run_manual_repair(max_attempts=max_attempts)
            return True
        if command == "/skills":
            self.print_skills()
            return True
        if command == "/dashboard":
            self.print_dashboard_summary()
            return True
        if command == "/live":
            self.append_message("system> live: rich-only in v0.5 fullscreen; Textual already refreshes its fixed panels")
            return True
        if command == "/cancel":
            self.request_cancel()
            return True
        if command == "/status":
            self.append_message(
                "system> "
                f"status: {self.state.status}; "
                f"verification: {self.state.verification}; "
                f"profile: {self.profile}; "
                f"changed_files: {len(self.state.changed_files)}"
            )
            return True
        self.append_message(f"system> Unknown command: {command}")
        return True

    def run_manual_verification(self) -> None:
        changed_files = list(self.state.changed_files)
        if not changed_files and self.last_result:
            changed_files = list(self.last_result.changed_files)
        result = self.runtime.verify_changed_files(
            changed_files,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            event_callback=self.handle_runtime_event,
            cancel_check=self.is_cancel_requested,
        )
        self.last_manual_verification = result
        self.append_message(f"system> manual verification: {result.status}")

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
            self.append_message("system> manual repair requires a failed verification result")
            return
        result = self.runtime.repair_after_failed_verification(
            task=task,
            changed_files=changed_files,
            failed_verification_results=failed_results,
            profile_name=self.profile,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            event_callback=self.handle_runtime_event,
            cancel_check=self.is_cancel_requested,
            max_attempts=max_attempts,
        )
        self.last_manual_repair = result
        self.state.changed_files = list(result.changed_files)
        self.state.verification = result.verification
        self.state.summary_path = result.summary_path
        self.state.repair_attempts = result.repair_attempts
        self.append_message(f"system> manual repair: {result.status}, verification: {result.verification}")

    def print_plan_preview(self, task: str | None = None) -> None:
        if not task:
            if not self.state.plan_summary:
                self.append_message("system> plan: no active plan")
                return
            self.append_message(
                "system> "
                f"plan: {self.state.plan_summary}; "
                f"status={self.state.plan_status or 'unknown'}; "
                f"steps={self.state.plan_step_count}"
            )
            return
        result = self.runtime.preview_plan(task, self.profile)
        parts = [
            f"system> plan preview: {result.status}",
            result.summary,
            f"steps={result.step_count}",
            f"context={result.context_used_tokens_estimate}/{result.context_budget_tokens}",
            f"trace={result.trace_path}",
        ]
        if result.risk_summary:
            parts.append("risks=" + "; ".join(result.risk_summary))
        self.append_message(" | ".join(parts))

    def set_mode(self, argument: str) -> None:
        if argument:
            self.state.mode = argument
        self.append_message(f"system> mode: {self.state.mode}")

    def print_model(self, profile_name: str | None = None) -> None:
        if profile_name:
            self.profile = profile_name
            self.append_message(f"system> active profile: {self.profile}")
            return
        profiles = load_profiles(self.workspace).profiles
        items = []
        for profile in profiles:
            marker = "*" if profile.name == self.profile else ""
            items.append(f"{profile.name}{marker} [{profile.provider}/{profile.model or ''}]")
        self.append_message("system> profiles: " + (" | ".join(items) if items else "none"))

    def print_skills(self) -> None:
        skill_root = self.workspace / ".xhx" / "skills"
        if not skill_root.exists():
            self.append_message("system> skills: none")
            return
        skills = [path.relative_to(self.workspace).as_posix() for path in sorted(skill_root.iterdir())]
        self.append_message("system> skills: " + (" | ".join(skills) if skills else "none"))

    def print_dashboard_summary(self) -> None:
        self.append_message(
            "system> "
            f"dashboard: status={self.state.status}; "
            f"run={self.state.run_id or 'none'}; "
            f"verification={self.state.verification}; "
            f"changed={len(self.state.changed_files)}; "
            f"context={self.state.context_used_tokens_estimate}/{self.state.context_budget_tokens or 0}; "
            f"events={len(self.state.events)}"
        )

    def request_cancel(self, reason: str = "Cancel requested by user.") -> bool:
        if self.state.status in {"idle", "success", "failed", "cancelled", "skipped_no_changes"}:
            self.append_message("system> No running task to cancel")
            return False
        self.cancel_requested = True
        self.handle_runtime_event(RuntimeEvent(type="cancel_requested", message=reason, payload={"source": "textual"}))
        self.append_message("system> Cancel requested. The current task will stop at the next safe runtime boundary.")
        return True

    def is_cancel_requested(self) -> bool:
        return self.cancel_requested

    def print_context_summary(self) -> None:
        languages = ", ".join(self.state.detected_languages) or "unknown"
        self.append_message(
            "system> "
            f"context: turn={self.state.context_turn or 'none'} "
            f"selected={self.state.context_selected} "
            f"omitted={self.state.context_omitted} "
            f"budget={self.state.context_used_tokens_estimate}/{self.state.context_budget_tokens or 0} "
            f"languages={languages} "
            f"files={self.state.file_count}"
        )

    def print_evidence_summary(self) -> None:
        if not self.state.policy_decisions:
            self.append_message("system> policy evidence: none")
            return
        items = [
            f"{item.source or item.scope}: {item.decision} ({item.risk}) {item.reason}"
            for item in self.state.policy_decisions[-3:]
        ]
        self.append_message("system> policy evidence: " + " | ".join(items))

    def print_diff_summary(self) -> None:
        changed_files = self.current_changed_files()
        if not changed_files:
            self.append_message("system> diff: no changed files")
            return
        result = self.runtime.diff_changed_files(changed_files)
        diff_excerpt = result.diff_text.strip()
        if len(diff_excerpt) > 600:
            diff_excerpt = "..." + diff_excerpt[-600:]
        parts = [result.summary]
        if diff_excerpt:
            parts.append(diff_excerpt)
        if result.risk_summary:
            parts.append("notes: " + "; ".join(result.risk_summary))
        self.append_message("system> diff: " + "\n".join(parts))

    def apply_runtime_event(self, event: RuntimeEvent) -> None:
        self.state.reduce(event)
        self.refresh_snapshot()

    def apply_run_result(self, result: RunResult) -> None:
        self.call_ui(self._apply_run_result, result)

    def _apply_run_result(self, result: RunResult) -> None:
        self.state.apply_result(result)
        self.refresh_snapshot()

    def append_message(self, message: str) -> None:
        self.call_ui(self._append_message, message)

    def _append_message(self, message: str) -> None:
        self.messages.append(message)
        self.refresh_snapshot()

    def call_ui(self, callback: Callable[..., T], *args: object) -> T | None:
        if self.ui_thread_id is not None and threading.get_ident() != self.ui_thread_id:
            try:
                return self.call_from_thread(callback, *args)
            except RuntimeError:
                pass
        return callback(*args)

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


def run_textual_console(
    *,
    workspace: Path | None = None,
    profile: str = "mock",
    auto_repair: bool = False,
    assume_yes: bool = False,
) -> None:
    TextualCommandConsoleApp(
        workspace=workspace,
        profile=profile,
        auto_repair=auto_repair,
        assume_yes=assume_yes,
    ).run()
