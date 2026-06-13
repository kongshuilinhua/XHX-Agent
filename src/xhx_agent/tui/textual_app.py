from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.suggester import Suggester
from textual.widgets import Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from xhx_agent.cli.completion import XhxCompleter
from xhx_agent.runtime.app import ManualRepairResult, ManualVerificationResult, RunResult, RuntimeApp
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.runtime.profiles import load_profiles
from xhx_agent.runtime.session import list_sessions, record_session, save_view_log
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.state import ConsoleState
from xhx_agent.tui.format import context_meter, human_tokens


SLASH_COMMAND_HINTS = [
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
    "/allow",
    "/deny",
    "/clear",
    "/new",
    "/sessions",
    "/resume",
    "/exit",
]

# Auto-memory imports for monkeypatching in unit tests
from xhx_agent.memory import list_memories, propose_memories, write_memory
from xhx_agent.memory.store import slugify
from xhx_agent.models import build_chat_client
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.profiles import get_profile


class XhxTextualSuggester(Suggester):
    def __init__(self, completer: XhxCompleter) -> None:
        super().__init__(case_sensitive=False)
        self.completer = completer

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        candidates = self.completer.get_completions(value)
        if candidates:
            return candidates[0]
        return None


T = TypeVar("T")


@dataclass
class PendingConfirmation:
    command: str
    decision: PolicyDecision
    event: threading.Event = field(default_factory=threading.Event)
    response: bool | None = None

    @property
    def summary(self) -> str:
        return f"{self.command} ({self.decision.risk.value})"


@dataclass(frozen=True)
class TextualSnapshot:
    header: str
    status_line: str
    conversation: str
    runtime_state: str
    changed_files: str
    details: str
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
        pending_confirmation: str | None = None,
        active_detail: str = "overview",
        detail_text: str = "",
    ) -> TextualSnapshot:
        run_id = state.run_id or "none"
        header = f"xhx-agent | {state.status} | profile: {profile} | run: {run_id}"
        streaming = getattr(state, "is_streaming", False)
        ctx_label, _, ctx_level = context_meter(
            state.context_used_tokens_estimate, state.context_budget_tokens
        )
        if ctx_level == "ok":
            ctx_str = f"[green]{ctx_label}[/green]"
        elif ctx_level == "warn":
            ctx_str = f"[yellow]{ctx_label}[/yellow]"
        elif ctx_level == "crit":
            ctx_str = f"[red]{ctx_label}[/red]"
        else:
            ctx_str = ctx_label

        status_line = (
            f"state: {state.status}  •  mode: {state.mode}  •  turn: {state.context_turn or 0}"
            f"  •  tokens: {human_tokens(state.tokens_total)}"
            f"  •  {ctx_str}"
            f"  •  verify: {state.verification}  •  changed: {len(state.changed_files)}"
            f"  •  streaming: {'yes' if streaming else 'no'}"
        )
        flags = []
        if auto_repair:
            flags.append("repair:on")
        if assume_yes:
            flags.append("yes:on")
        # Conversation has a single source of truth: the append-only message history.
        # An in-flight model answer is shown as ONE ephemeral `model (streaming…)>` line that
        # disappears once the turn commits its `assistant>` line to history — so the answer is
        # never rendered twice. Only when there is no history yet (e.g. a snapshot built directly
        # from runtime events, as in unit tests) do we reconstruct a preview from state.
        textual_messages = getattr(state, "textual_messages", None)
        conversation_lines: list[str] = []
        if textual_messages:
            conversation_lines.extend(str(item) for item in textual_messages)
            if streaming and state.model_output:
                model_text = " ".join(state.model_output.split())
                conversation_lines.append(f"model (streaming…)> {model_text}▌")
        else:
            if state.task:
                task_text = state.task
                if task_text.startswith("Follow-up task in the same console session."):
                    if "\nUser request:\n" in task_text:
                        parts = task_text.split("\nUser request:\n", 1)
                        if len(parts) > 1:
                            subparts = parts[1].split("\n\nPrevious run context:", 1)
                            task_text = subparts[0].strip()
                conversation_lines.append(f"user> {task_text}")
            if state.plan_summary:
                conversation_lines.append(f"plan> {state.plan_summary}")
            if state.model_output:
                model_text = " ".join(state.model_output.split())
                if streaming:
                    conversation_lines.append(f"model (streaming…)> {model_text}▌")
                else:
                    conversation_lines.append(f"model> {model_text}")
            if state.summary_path:
                conversation_lines.append(f"summary> {state.summary_path}")
            if state.cancel_requested:
                conversation_lines.append(f"cancel> {state.cancel_reason or 'requested'}")
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
        if pending_confirmation:
            permission_state += f"\nwaiting: {pending_confirmation}"
        elif pending_policy is not None:
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
        details = f"{active_detail}\n\n{detail_text or 'No detail selected.'}"
        commands = " ".join(SLASH_COMMAND_HINTS)
        return cls(
            header=header,
            status_line=status_line,
            conversation="\n".join(conversation_lines),
            runtime_state=runtime_state,
            changed_files=changed_files,
            details=details,
            commands=commands,
        )


def get_clipboard_text() -> str | None:
    import sys
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.CloseClipboard.argtypes = []
        user32.CloseClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL

        CF_UNICODETEXT = 13

        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                return None
            try:
                text = ctypes.wstring_at(ptr)
                if text and len(text) > 65536:
                    text = text[:65536]
                return text
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:
        return None


class WrappingOptionList(OptionList):
    """OptionList whose cursor wraps around at both ends.

    The single home for wrap-around navigation: pressing up on the first option jumps to the
    last, and down on the last returns to the first. Used by every picker — modal pickers focus
    this widget (so its native bindings give wrap for free), while the inline autocomplete picker
    keeps input focus and drives the very same ``action_cursor_*`` methods from ``on_key``.
    """

    def action_cursor_up(self) -> None:
        count = self.option_count
        if count:
            self.highlighted = count - 1 if self.highlighted in (None, 0) else self.highlighted - 1

    def action_cursor_down(self) -> None:
        count = self.option_count
        if count:
            self.highlighted = 0 if (self.highlighted is None or self.highlighted >= count - 1) else self.highlighted + 1


class TextualCommandConsoleApp(App[None]):
    """Fullscreen v0.5 shell that renders ConsoleState without owning Runtime internals."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #statusline {
        height: 1;
        color: $accent;
        text-style: bold;
        background: $panel;
        padding: 0 1;
    }

    #body {
        height: 1fr;
    }

    #conversation_scroll {
        width: 1fr;
        border: solid $primary;
    }

    #conversation {
        width: 100%;
        height: auto;
        padding: 1;
    }

    #input {
        height: 3;
    }

    #interactive_container {
        height: auto;
        max-height: 12;
        margin-top: 1;
        display: none;
    }

    #active_options {
        border: solid $accent;
        height: auto;
        background: $panel;
    }

    OptionList > .option-list--option-highlighted {
        background: $accent;
        color: $text;
        text-style: bold;
    }
    """

    BINDINGS = [
        ("ctrl+c", "cancel_task", "Cancel"),
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
        permission_timeout_seconds: float = 300.0,
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
        # The real model-facing conversation history (full message dicts) carried across turns so
        # the model actually remembers the dialogue. None until the first turn/resume populates it.
        self.prior_messages: list[dict] | None = None
        # Stable id for the current conversation; every turn records under it so the resume picker
        # shows one entry per conversation instead of one per turn.
        self.conversation_id: str = uuid.uuid4().hex
        self.exit_requested = False
        self.cancel_requested = False
        self.pending_steer: str | None = None
        self.pending_confirmation: PendingConfirmation | None = None
        # When a picker is active, holds the callback to run with the chosen option id
        # (or None if dismissed). This replaces stringly-typed active_detail dispatch.
        self._picker_on_select: Callable[[str | None], None] | None = None
        self.permission_timeout_seconds = permission_timeout_seconds
        self.active_detail = "overview"
        self.detail_text = (
            "Use /plan, /context, /evidence, /diff, /verify, /repair, or /dashboard to inspect runtime state."
        )
        self.widgets_ready = False
        self.ui_thread_id: int | None = None
        self.auto_memory = True

    @property
    def clipboard(self) -> str:
        text = get_clipboard_text()
        if text is not None:
            # Replace newlines with spaces for single-line input pasting
            return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        return getattr(self, "_clipboard", "")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(id="statusline")
        with Horizontal(id="body"), VerticalScroll(id="conversation_scroll"):
            yield Static(id="conversation")
            yield Vertical(id="interactive_container")
        completer = XhxCompleter(self.workspace)
        yield Input(
            placeholder="Type a task or slash command. Press Tab or Right arrow to complete.",
            id="input",
            suggester=XhxTextualSuggester(completer),
        )
        yield Footer()

    def on_mount(self) -> None:
        self.ui_thread_id = threading.get_ident()
        self.widgets_ready = True
        self.refresh_snapshot()
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

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
            pending_confirmation=self.pending_confirmation.summary if self.pending_confirmation else None,
            active_detail=self.active_detail,
            detail_text=self.detail_text,
        )
        self.title = snapshot.header
        if not self.widgets_ready:
            return
        try:
            self.query_one("#statusline", Static).update(snapshot.status_line)
            self.query_one("#conversation", Static).update(snapshot.conversation)
        except Exception:
            pass

        # Only scroll to end when conversation text grows, and defer it after layout pass
        conv_text = snapshot.conversation
        last_len = getattr(self, "_last_conv_len", 0)
        if len(conv_text) > last_len:
            self._last_conv_len = len(conv_text)
            self.call_after_refresh(self.query_one("#conversation_scroll").scroll_end, animate=False)
        elif len(conv_text) < last_len:
            self._last_conv_len = len(conv_text)

    def action_clear(self) -> None:
        self.state = ConsoleState()
        self.messages.clear()
        # Start a fresh conversation: drop the model-facing history and start a new conversation id.
        self.prior_messages = None
        self.last_result = None
        self.conversation_id = uuid.uuid4().hex
        self.active_detail = "overview"
        self.detail_text = (
            "Use /plan, /context, /evidence, /diff, /verify, /repair, or /dashboard to inspect runtime state."
        )
        self.refresh_snapshot()

    def action_cancel_task(self) -> None:
        if self.is_task_running():
            self.request_cancel("Keyboard interrupt requested cancellation.")
        else:
            self.append_message("system> Use /exit to quit the console.")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        stripped = event.value.strip()
        if stripped.startswith("/"):
            parts = stripped.split(" ", 1)
            cmd = parts[0]
            valid_commands = {
                "/help", "/model", "/status", "/plan", "/context", "/evidence",
                "/diff", "/verify", "/repair", "/skills", "/mode", "/dashboard",
                "/cancel", "/live", "/allow", "/deny", "/clear", "/new", "/sessions",
                "/resume", "/exit"
            }
            if cmd in valid_commands:
                self.hide_interactive_container()
                event.input.value = ""
                should_continue = self.handle_text_input(stripped, use_worker=True)
                self.refresh_snapshot()
                if not should_continue:
                    self.exit()
                return

        try:
            container = self.query_one("#interactive_container")
            if container.styles.display == "block":
                active_options = self.query_one("#active_options", OptionList)
                if active_options.highlighted is not None:
                    opt = active_options.get_option_at_index(active_options.highlighted)
                    event.input.value = ""
                    self.resolve_interactive_selection(opt.id)
                    return
        except Exception:
            pass

        event.input.value = ""
        should_continue = self.handle_text_input(event.value, use_worker=True)
        self.refresh_snapshot()
        if not should_continue:
            self.exit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.value.startswith("/"):
            valid_commands = {
                "/help", "/model", "/status", "/plan", "/context", "/evidence",
                "/diff", "/verify", "/repair", "/skills", "/mode", "/dashboard",
                "/cancel", "/live", "/allow", "/deny", "/clear", "/new", "/sessions",
                "/resume", "/exit"
            }
            if event.value in valid_commands:
                self.hide_interactive_container()
            else:
                self.show_slash_commands(event.value)
        else:
            if self.active_detail == "commands":
                self.hide_interactive_container()

    def on_input_blur(self, event: events.Blur) -> None:
        try:
            self.query_one("#input", Input).focus()
        except Exception:
            pass

    def handle_text_input(self, text: str, *, use_worker: bool = False) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self.handle_slash_command(stripped, use_worker=use_worker)
        if self.is_task_running():
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

    @property
    def orchestrator_mode(self) -> str | None:
        """Explicit orchestrator paradigm from /mode, or None to auto-classify."""
        return self.state.mode if self.state.mode in {"plan", "loop", "graph", "linear", "dag"} else None

    def run_task(self, task: str, *, announce_user: bool = True, reset_cancel: bool = True) -> None:
        if reset_cancel:
            self.cancel_requested = False
        if announce_user:
            self.append_message(f"user> {task}")
        # When we already hold the real conversation history, pass the raw task as the new user
        # turn and let prior_messages carry the memory. Only fall back to the metadata follow-up
        # summary when there is no transcript to restore (e.g. an old session without one).
        if self.prior_messages:
            runtime_task = task
        else:
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
            mode=self.orchestrator_mode,
            prior_messages=self.prior_messages,
        )
        self.last_result = result
        self.apply_run_result(result)
        self.append_message(f"system> run finished: {result.status}, verification: {result.verification}")
        view_path = save_view_log(self.workspace, result.run_id, self.messages)
        turn_count = sum(1 for e in list_sessions(self.workspace) if e.conversation_id == self.conversation_id) + 1
        record_session(self.workspace, task, result, conversation_id=self.conversation_id, view_path=view_path, turn_count=turn_count)
        # Carry the full conversation forward: this run's transcript already includes the prior
        # history we passed in, so the next turn restores complete context (real memory).
        self._refresh_prior_messages(result)
        self._maybe_suggest_memories(result)
        self.run_pending_steer()

    def _refresh_prior_messages(self, result: RunResult) -> None:
        """Reload the just-finished run's transcript so the next turn keeps full memory."""
        path = getattr(result, "transcript_path", None)
        if not path:
            return
        try:
            from xhx_agent.runtime.session import load_transcript_messages

            messages = load_transcript_messages(self.workspace, path)
            if messages:
                self.prior_messages = messages
        except Exception:
            pass

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

    def is_task_running(self) -> bool:
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
        if self.next_confirm_response is not None:
            allowed = bool(self.next_confirm_response)
            self.next_confirm_response = None
            self.append_permission_result(command, decision, allowed)
            return allowed
        if self.can_wait_for_interactive_confirmation():
            confirmation = PendingConfirmation(command=command, decision=decision)
            self.call_ui(self.open_pending_confirmation, confirmation)
            if not confirmation.event.wait(self.permission_timeout_seconds):
                self.call_ui(self.timeout_pending_confirmation, confirmation)
            allowed = bool(confirmation.response)
            self.call_ui(self.close_pending_confirmation, confirmation)
            self.append_permission_result(command, decision, allowed)
            return allowed
        self.append_permission_result(command, decision, False)
        return False

    def handle_slash_command(self, command_line: str, *, use_worker: bool = False) -> bool:
        command, _, argument = command_line.partition(" ")
        argument = argument.strip()
        if command == "/exit":
            self.exit_requested = True
            return False
        if command == "/clear":
            self.action_clear()
            return True
        if command == "/new":
            self.action_clear()
            return True
        if command == "/allow":
            if not self.resolve_pending_confirmation(True):
                self.next_confirm_response = True
                self.append_message("system> next permission prompt will be allowed once")
            return True
        if command == "/deny":
            if not self.resolve_pending_confirmation(False):
                self.next_confirm_response = False
                self.append_message("system> next permission prompt will be declined once")
            return True
        if command == "/help":
            cmds_list = [
                "/help      - Show help message and command details",
                "/model     - List or switch active profile",
                "/status    - Show current agent status",
                "/plan      - Show active plan or preview a task plan",
                "/context   - Show current context pack budget & files",
                "/evidence  - Show recent safety policy evidence",
                "/diff      - Show git diff summary for changes",
                "/verify    - Run verification for changed files",
                "/repair    - Repair codebase after failed verification",
                "/skills    - List available skill directories",
                "/mode      - Show or set orchestrator execution mode",
                "/dashboard - Print detailed dashboard runtime state",
                "/cancel    - Request task cancellation at safe boundary",
                "/live      - Toggle live streaming dashboard render",
                "/allow     - Answer allow to pending permission confirmation",
                "/deny      - Answer deny to pending permission confirmation",
                "/clear     - Clear conversation messages and details",
                "/new       - Clear conversation and start a new session",
                "/sessions  - List recent recorded agent sessions (supports filtering by keyword)",
                "/sessions clear - 清理无法恢复的旧会话",
                "/resume    - Switch follow-up context to a past session (supports prefix/suffix)",
                "/exit      - Exit the textual command console",
            ]
            self.append_message(
                "system> available commands:\n" + "\n".join(cmds_list)
            )
            self.set_detail(
                "help",
                "\n".join(
                    [
                        "/model [name] - list or switch profiles",
                        "/plan [task] - show current plan or dry-run a plan",
                        "/context - show current context pack summary",
                        "/evidence - show recent policy evidence",
                        "/diff - show changed files and git diff excerpt",
                        "/verify - run verification for changed files",
                        "/repair [loop] - repair after failed verification",
                        "/allow or /deny - answer pending confirmation",
                        "/cancel - request safe-boundary cancellation",
                        "/sessions [keyword] - list past sessions and filter by keyword",
                        "/sessions clear - 清理无法恢复的旧会话",
                        "/new - start a new session",
                        "/resume <run_id_prefix> - switch follow-up context to a past session",
                    ]
                ),
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
            if use_worker and self.widgets_ready:
                self.start_manual_verification_worker()
            else:
                self.run_manual_verification()
            return True
        if command == "/repair":
            max_attempts = 2 if argument.lower() in {"loop", "auto"} else 1
            if use_worker and self.widgets_ready:
                self.start_manual_repair_worker(max_attempts=max_attempts)
            else:
                self.run_manual_repair(max_attempts=max_attempts)
            return True
        if command == "/skills":
            self.print_skills()
            return True
        if command == "/dashboard":
            self.print_dashboard_summary()
            return True
        if command == "/live":
            self.append_message(
                "system> live: rich-only in v0.5 fullscreen; Textual already refreshes its fixed panels"
            )
            return True
        if command == "/cancel":
            self.request_cancel()
            return True
        if command == "/sessions":
            if argument.strip() == "clear":
                from xhx_agent.runtime.session import prune_legacy_sessions
                n = prune_legacy_sessions(self.workspace)
                self.append_message(f"system> 已清理 {n} 条旧会话")
                self.refresh_snapshot()
            else:
                self.handle_sessions(argument)
            return True
        if command == "/resume":
            self.handle_resume(argument)
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
        self.set_detail(
            "verify",
            "\n".join(
                [
                    f"status: {result.status}",
                    f"changed_files: {', '.join(result.changed_files) or 'none'}",
                    f"commands: {', '.join(result.commands) or 'none'}",
                    f"summary: {result.summary_path}",
                    f"risks: {'; '.join(result.risk_summary) if result.risk_summary else 'none'}",
                ]
            ),
        )

    def start_manual_verification_worker(self) -> None:
        self.cancel_requested = False
        self.state.status = "verifying"
        self.append_message("system> manual verification started")
        self.run_worker(
            self.run_manual_verification,
            name="manual-verification",
            group="runtime",
            thread=True,
        )

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
        self.set_detail(
            "repair",
            "\n".join(
                [
                    f"status: {result.status}",
                    f"verification: {result.verification}",
                    f"attempts: {result.repair_attempts}",
                    f"changed_files: {', '.join(result.changed_files) or 'none'}",
                    f"commands: {', '.join(result.commands) or 'none'}",
                    f"summary: {result.summary_path}",
                    f"risks: {'; '.join(result.risk_summary) if result.risk_summary else 'none'}",
                ]
            ),
        )

    def start_manual_repair_worker(self, max_attempts: int = 1) -> None:
        self.cancel_requested = False
        self.state.status = "repairing"
        self.append_message("system> manual repair started")
        self.run_worker(
            lambda: self.run_manual_repair(max_attempts=max_attempts),
            name="manual-repair",
            group="runtime",
            thread=True,
        )

    def print_plan_preview(self, task: str | None = None) -> None:
        if not task:
            if not self.state.plan_summary:
                self.append_message("system> plan: no active plan")
                self.set_detail("plan", "No active plan.")
                return
            detail = "\n".join(
                [
                    f"summary: {self.state.plan_summary}",
                    f"status: {self.state.plan_status or 'unknown'}",
                    f"steps: {self.state.plan_step_count}",
                ]
            )
            self.append_message(
                "system> "
                f"plan: {self.state.plan_summary}; "
                f"status={self.state.plan_status or 'unknown'}; "
                f"steps={self.state.plan_step_count}"
            )
            self.set_detail("plan", detail)
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
        self.set_detail(
            "plan",
            "\n".join(
                [
                    f"status: {result.status}",
                    f"summary: {result.summary}",
                    f"steps: {result.step_count}",
                    f"context: {result.context_used_tokens_estimate}/{result.context_budget_tokens}",
                    f"trace: {result.trace_path}",
                    f"risks: {'; '.join(result.risk_summary) if result.risk_summary else 'none'}",
                ]
            ),
        )

    def set_mode(self, argument: str) -> None:
        if argument:
            self.state.mode = argument
            self.append_message(f"system> mode: {self.state.mode}")
            self.set_detail("mode", f"active mode: {self.state.mode}")
            return
        # No argument: show current mode plus a selectable picker (Arrow keys to navigate,
        # Enter to apply directly). Mirrors the /model profile picker.
        self.append_message(f"system> mode: {self.state.mode} (select to switch)")
        # The three real paradigms only. linear/dag are converged supporting mechanisms
        # (linear → plan's stop policy, dag → graph's execution layer), reachable via
        # `--mode linear/dag` for the preserved legacy paths but not shown as paradigms here.
        options = [
            ("loop — ReAct tool-use loop (default)", "loop"),
            ("plan — plan-and-execute (batch)", "plan"),
            ("graph — multi-agent workflow", "graph"),
        ]
        self.set_detail("mode", "Select an orchestrator paradigm with Arrow keys + Enter.")
        self.present_picker(options, on_select=self._select_mode, title="Select Mode")

    def print_model(self, profile_name: str | None = None) -> None:
        if profile_name:
            self.profile = profile_name
            self.append_message(f"system> active profile: {self.profile}")
            self.set_detail("model", f"active profile: {self.profile}")
            return
        profiles = load_profiles(self.workspace).profiles
        if not profiles:
            self.append_message("system> profiles: none")
            self.set_detail("model", "none")
            return
        items = []
        for profile in profiles:
            marker = "*" if profile.name == self.profile else ""
            items.append(f"{profile.name}{marker} [{profile.provider}/{profile.model or ''}]")
        self.append_message("system> profiles:\n" + "\n".join(items))
        self.set_detail("model", "\n".join(items))

        options = [(f"{p.name} [{p.provider}/{p.model or ''}]", p.name) for p in profiles]
        self.present_picker(options, on_select=self._select_profile, title="Select Profile")

    def print_skills(self) -> None:
        import re
        skill_root = self.workspace / ".xhx" / "skills"
        if not skill_root.exists():
            self.append_message("system> skills: none")
            self.set_detail("skills", "none")
            return

        # Find folders and maximum length of /{folder_name}
        folders = []
        max_len = 0
        for path in sorted(skill_root.iterdir()):
            if path.is_dir():
                name_str = f"/{path.name}"
                folders.append((path, name_str))
                max_len = max(max_len, len(name_str))

        pad_width = max_len + 4  # align descriptions with 4 spaces padding

        lines = []
        for path, name_str in folders:
            description = ""
            md_path = path / "SKILL.md"
            if md_path.exists():
                try:
                    content = md_path.read_text(encoding="utf-8")
                    match = re.match(r"^---\s*(?:yaml)?\r?\n(.*?)\r?\n---\r?\n", content, re.DOTALL | re.IGNORECASE)
                    if match:
                        yaml_text = match.group(1)
                        desc_match = re.search(r"^description:\s*(.*)$", yaml_text, re.MULTILINE)
                        if desc_match:
                            desc_val = desc_match.group(1).strip()
                            if (desc_val.startswith('"') and desc_val.endswith('"')) or (desc_val.startswith("'") and desc_val.endswith("'")):
                                desc_val = desc_val[1:-1]
                            description = desc_val
                except Exception:
                    pass
            else:
                json_path = path / "SKILL.json"
                if json_path.exists():
                    try:
                        import json
                        with open(json_path, encoding="utf-8") as f:
                            data = json.load(f)
                            description = data.get("description", "")
                    except Exception:
                        pass

            padded_name = f"{name_str:<{pad_width}}"
            if description:
                lines.append(f"{padded_name}{description}")
            else:
                lines.append(name_str)

        self.append_message("system> skills:\n" + "\n".join(lines))
        self.set_detail("skills", "\n".join(lines))

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
        self.set_detail(
            "dashboard",
            "\n".join(
                [
                    f"status: {self.state.status}",
                    f"run: {self.state.run_id or 'none'}",
                    f"verification: {self.state.verification}",
                    f"changed_files: {len(self.state.changed_files)}",
                    f"context: {self.state.context_used_tokens_estimate}/{self.state.context_budget_tokens or 0}",
                    f"events: {len(self.state.events)}",
                    f"pending_steer: {self.pending_steer or 'none'}",
                    f"pending_confirm: {self.pending_confirmation.summary if self.pending_confirmation else 'none'}",
                ]
            ),
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
        from xhx_agent.tui.format import context_meter, human_tokens

        used = self.state.context_used_tokens_estimate
        budget = self.state.context_budget_tokens
        label, pct, level = context_meter(used, budget)

        detail_lines = []
        if budget <= 0:
            detail_lines.append("Context —")
        else:
            bar_len = 30
            filled = int(round((pct / 100) * bar_len)) if pct is not None else 0
            filled = max(0, min(bar_len, filled))
            bar_str = "█" * filled + "░" * (bar_len - filled)
            if level == "ok":
                bar_line = f"[green]{bar_str}[/green]"
            elif level == "warn":
                bar_line = f"[yellow]{bar_str}[/yellow]"
            elif level == "crit":
                bar_line = f"[red]{bar_str}[/red]"
            else:
                bar_line = bar_str

            pct_val = pct if pct is not None else 0.0
            detail_lines.extend([
                f"Context {human_tokens(used)} / {human_tokens(budget)} ({pct_val:.1f}%)",
                bar_line,
            ])

        detail_lines.append(f"── 本轮 (turn {self.state.context_turn or 0})")
        detail_lines.append(
            f"   选中文件 {self.state.context_selected} · 省略 {self.state.context_omitted} · 预算 {human_tokens(budget)}"
        )
        detail_lines.append("── token")
        detail_lines.append(
            f"   最近调用 prompt {human_tokens(self.state.tokens_prompt)} · "
            f"completion {human_tokens(self.state.tokens_completion)} · "
            f"累计 {human_tokens(self.state.tokens_total)}"
        )
        if self.state.compaction_count > 0:
            detail_lines.append("── 压缩 (microcompact)")
            detail_lines.append(
                f"   已压缩 {self.state.compaction_count} 次 (最近 "
                f"{self.state.compaction_last_before}→{self.state.compaction_last_after} 条)"
            )

        self.append_message(f"system> {label}")
        self.set_detail("context", "\n".join(detail_lines))

    def print_evidence_summary(self) -> None:
        if not self.state.policy_decisions:
            self.append_message("system> policy evidence: none")
            self.set_detail("evidence", "policy evidence: none")
            return
        items = [
            f"{item.source or item.scope}: {item.decision} ({item.risk}) {item.reason}"
            for item in self.state.policy_decisions[-3:]
        ]
        self.append_message("system> policy evidence: " + " | ".join(items))
        self.set_detail("evidence", "\n".join(items))

    def print_diff_summary(self) -> None:
        changed_files = self.current_changed_files()
        if not changed_files:
            self.append_message("system> diff: no changed files")
            self.set_detail("diff", "No changed files.")
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
        detail = "\n".join(parts)
        self.append_message("system> diff: " + detail)
        self.set_detail("diff", detail)

    def _timeline_line_for_event(self, event: RuntimeEvent) -> str | None:
        """把运行时事件翻译成一行时间线文本；不可见事件返回 None。

        只翻译当前不产生消息行的事件，避免与已有 append 重复或打乱 messages 索引。
        policy_decision 故意不在此（权限可见性已由 picker + permission 消息覆盖）。
        """
        et = event.type
        p = event.payload or {}
        if et == "tool_start":
            return f"  ⟶ tool  {p.get('tool', '?')}"
        if et == "tool_result":
            summary = (p.get("summary") or event.message or "").strip().replace("\n", " ")
            if len(summary) > 80:
                summary = summary[:80] + "…"
            glyph = "✗" if str(p.get("status")) in {"failed", "error"} else "✓"
            tail = f" → {summary}" if summary else ""
            return f"  {glyph} tool  {p.get('tool', '?')}{tail}"
        if et in {"graph_coordinator", "graph_worker", "graph_execute", "graph_review"}:
            role = et.removeprefix("graph_")
            msg = (event.message or "").strip().replace("\n", " ")
            if len(msg) > 100:
                msg = msg[:100] + "…"
            return f"▸ agent  {role}  {msg}".rstrip()
        if et == "verification_start":
            return f"  ⚙ verify  {p.get('command', '')}"
        if et == "verification_result":
            code = p.get("exit_code")
            tail = f"(exit {code})" if code is not None else ""
            return f"  ⚙ verify  {p.get('command', '')} → {p.get('status', '')}{tail}"
        if et == "model_plan":
            return f"plan> {event.message}"
        return None

    def apply_runtime_event(self, event: RuntimeEvent) -> None:
        self.state.reduce(event)
        line = self._timeline_line_for_event(event)
        if line is not None:
            # 单一有序流：事件行与 user>/assistant>/system> 共用 self.messages，时序天然正确。
            self.messages.append(line)
        self.refresh_snapshot()

    def can_wait_for_interactive_confirmation(self) -> bool:
        return self.widgets_ready and self.ui_thread_id is not None and threading.get_ident() != self.ui_thread_id

    def open_pending_confirmation(self, confirmation: PendingConfirmation) -> None:
        self.pending_confirmation = confirmation
        self._append_message(f"system> permission required: {confirmation.summary}; use /allow or /deny")
        self.present_picker(
            [("Allow (Run Command)", "allow"), ("Deny (Cancel Command)", "deny")],
            on_select=self._select_confirmation,
            title="Permission Confirmation",
        )

    def resolve_pending_confirmation(self, response: bool) -> bool:
        confirmation = self.pending_confirmation
        if confirmation is None or confirmation.event.is_set():
            return False
        confirmation.response = response
        confirmation.event.set()
        decision = "allowed" if response else "declined"
        self.append_message(f"system> pending permission {decision}")
        return True

    def timeout_pending_confirmation(self, confirmation: PendingConfirmation) -> None:
        if self.pending_confirmation is not confirmation or confirmation.event.is_set():
            return
        confirmation.response = False
        confirmation.event.set()
        self._append_message(f"system> permission timed out and was declined: {confirmation.command}")

    def close_pending_confirmation(self, confirmation: PendingConfirmation) -> None:
        if self.pending_confirmation is confirmation:
            self.pending_confirmation = None
            self._picker_on_select = None
            self._dismiss_picker_widget()
            try:
                input_widget = self.query_one("#input", Input)
                input_widget.disabled = False
                input_widget.placeholder = "Type a task or slash command. Press Tab or Right arrow to complete."
                input_widget.focus()
            except Exception:
                pass
            self.refresh_snapshot()

    def append_permission_result(self, command: str, decision: PolicyDecision, allowed: bool) -> None:
        verb = "allowed" if allowed else "declined"
        self.append_message(f"system> permission {verb}: {command} ({decision.risk.value})")

    def apply_run_result(self, result: RunResult) -> None:
        self.call_ui(self._apply_run_result, result)

    def _apply_run_result(self, result: RunResult) -> None:
        self.state.apply_result(result)
        # Answer first (what the user asked for), then the run-log path as a trailing meta line.
        if result.answer:
            self._append_message(f"assistant> {result.answer}")
        if result.summary_path:
            self._append_message(f"summary> {result.summary_path}")
        self.refresh_snapshot()

    def _maybe_suggest_memories(self, result: RunResult) -> None:
        """跑完成功后自动抽候选记忆并逐条确认写入。best-effort：任何异常静默跳过，绝不影响任务。

        在 worker 线程执行（propose_memories 是一次模型调用）；确认 UI 经 call_ui 投到 UI 线程。
        mock profile 下 propose_memories 确定性返回空 → 无任何提示。
        """
        if not self.auto_memory or result.status != "success":
            return
        try:
            task = self.state.task or ""
            answer = (getattr(result, "answer", None) or "").strip()
            digest = f"Assistant: {answer}" if answer else f"Run status: {result.status}"
            config = load_config(self.workspace)
            profile = get_profile(self.workspace, self.profile or config.default_profile)
            client = build_chat_client(profile)
            existing = {slugify(r.name) for r in list_memories(self.workspace)}
            candidates = propose_memories(client, task, digest, existing_names=existing)
            for candidate in candidates:
                if self._confirm_memory_blocking(candidate):
                    write_memory(
                        self.workspace,
                        name=candidate.name,
                        description=candidate.description,
                        mtype=candidate.mtype,
                        body=candidate.body,
                    )
                    self.append_message(f"system> Remembered: {candidate.name}")
        except Exception:
            return

    def _confirm_memory_blocking(self, candidate) -> bool:
        """在 worker 线程阻塞，弹一个 记住/跳过 picker，等用户选。非交互/超时一律视为否。"""
        if not self.can_wait_for_interactive_confirmation():
            return False
        done = threading.Event()
        holder = {"resp": False}

        def on_select(selected_id: str | None) -> None:
            holder["resp"] = selected_id == "remember"
            if not done.is_set():
                done.set()

        def show() -> None:
            self._append_message(
                f"system> 记忆候选 [{candidate.mtype}] {candidate.name}: {candidate.description}"
            )
            self.present_picker(
                [("记住（写入长期记忆）", "remember"), ("跳过", "skip")],
                on_select=on_select,
                title="Remember across sessions?",
            )

        self.call_ui(show)
        if not done.wait(self.permission_timeout_seconds):
            holder["resp"] = False
            self.call_ui(lambda: self.resolve_interactive_selection(None))
            done.wait(1.0)
        return bool(holder["resp"])

    def append_message(self, message: str) -> None:
        self.call_ui(self._append_message, message)

    def _append_message(self, message: str) -> None:
        self.messages.append(message)
        self.refresh_snapshot()

    def set_detail(self, title: str, text: str) -> None:
        self.call_ui(self._set_detail, title, text)

    def _set_detail(self, title: str, text: str) -> None:
        self.active_detail = title
        self.detail_text = text
        self.refresh_snapshot()

    def show_slash_commands(self, filter_prefix: str = "/") -> None:
        all_commands = [
            ("/help", "Show help message and command details"),
            ("/model", "List or switch active profile"),
            ("/status", "Show current agent status"),
            ("/plan", "Show active plan or preview a task plan"),
            ("/context", "Show current context pack budget & files"),
            ("/evidence", "Show recent safety policy evidence"),
            ("/diff", "Show git diff summary for changes"),
            ("/verify", "Run verification for changed files"),
            ("/repair", "Repair codebase after failed verification"),
            ("/skills", "List available skill directories"),
            ("/mode", "Show or set orchestrator execution mode"),
            ("/dashboard", "Print detailed dashboard runtime state"),
            ("/cancel", "Request task cancellation at safe boundary"),
            ("/live", "Toggle live streaming dashboard render"),
            ("/allow", "Allow the pending terminal command policy"),
            ("/deny", "Decline the pending terminal command policy"),
            ("/clear", "Clear conversation messages and details"),
            ("/sessions", "List recent recorded agent sessions"),
            ("/resume", "Switch follow-up context to a past session"),
            ("/exit", "Exit the textual command console"),
        ]
        filtered = [
            (f"{cmd:<10} | {desc}", cmd)
            for cmd, desc in all_commands
            if cmd.startswith(filter_prefix)
        ]
        if not filtered:
            self.hide_interactive_container()
            return
        self.active_detail = "commands"
        self.detail_text = "Select a slash command from the interactive list."
        self.present_picker(filtered, on_select=self._select_command, title="Select Command")

    def _dismiss_picker_widget(self) -> None:
        """Remove the active option list and hide its container. The single teardown used everywhere."""
        try:
            old = self.query_one("#active_options")
            old.remove()
            container = self.query_one("#interactive_container", Vertical)
            container._nodes._remove(old)
        except Exception:
            pass
        try:
            self.query_one("#interactive_container", Vertical).styles.display = "none"
        except Exception:
            pass

    def present_picker(
        self,
        options: list[tuple[Any, str]],
        *,
        on_select: Callable[[str | None], None],
        title: str = "Select",
        placeholder: str | None = None,
    ) -> None:
        """Mount the one and only wrap-around picker. Every selectable list goes through here.

        The input keeps focus; Arrow keys (handled in ``on_key``) drive the WrappingOptionList with
        wrap-around, Enter selects the highlighted option, Esc dismisses. ``on_select`` is invoked
        with the chosen option id (or None when dismissed) — replacing per-command branching.
        """
        if not self.widgets_ready:
            return
        self._dismiss_picker_widget()
        self._picker_on_select = on_select
        option_widgets = [Option(prompt, id=opt_id) for prompt, opt_id in options]
        option_list = WrappingOptionList(*option_widgets, id="active_options")
        container = self.query_one("#interactive_container", Vertical)
        container.mount(option_list)
        container.styles.display = "block"
        option_list.highlighted = 0
        input_widget = self.query_one("#input", Input)
        input_widget.disabled = False
        input_widget.placeholder = placeholder or f"[{title}] ↑/↓ navigate · Enter select · Esc cancel"
        input_widget.focus()
        self.call_after_refresh(self.query_one("#conversation_scroll").scroll_end, animate=False)

    def hide_interactive_container(self) -> None:
        """Dismiss any active picker without selecting (used while typing slash commands)."""
        self._picker_on_select = None
        self._dismiss_picker_widget()
        if self.active_detail == "commands":
            self.active_detail = "overview"
            self.detail_text = (
                "Use /plan, /context, /evidence, /diff, /verify, /repair, or /dashboard to inspect runtime state."
            )
        self.refresh_snapshot()

    # --- Per-command selection callbacks (the only place that differs between pickers) ---

    def _select_profile(self, selected_id: str | None) -> None:
        if selected_id:
            self.print_model(selected_id)

    def _select_mode(self, selected_id: str | None) -> None:
        if selected_id:
            self.set_mode(selected_id)

    def _select_session(self, selected_id: str | None) -> None:
        if selected_id:
            self.handle_resume(selected_id)

    def _select_confirmation(self, selected_id: str | None) -> None:
        # None (Esc/dismiss) or "deny" decline; only "allow" allows.
        self.resolve_pending_confirmation(selected_id == "allow")

    def _select_command(self, selected_id: str | None) -> None:
        if not selected_id:
            self.refresh_snapshot()
            return
        needs_args = selected_id in {"/model", "/plan", "/repair", "/mode", "/resume"}
        input_widget = self.query_one("#input", Input)
        input_widget.value = selected_id + (" " if needs_args else "")
        input_widget.focus()
        self.refresh_snapshot()

    def resolve_interactive_selection(self, selected_id: str | None) -> None:
        """Single resolution point for every picker: tear down, restore input, run the callback."""
        callback = self._picker_on_select
        self._picker_on_select = None
        self._dismiss_picker_widget()

        try:
            input_widget = self.query_one("#input", Input)
            input_widget.disabled = False
            input_widget.placeholder = "Type a task or slash command. Press Tab or Right arrow to complete."
            input_widget.focus()
        except Exception:
            pass

        if self.active_detail in {"sessions", "model", "commands", "mode"}:
            self.active_detail = "overview"
            self.detail_text = (
                "Use /plan, /context, /evidence, /diff, /verify, /repair, or /dashboard to inspect runtime state."
            )

        if callback is not None:
            callback(selected_id)
        else:
            self.refresh_snapshot()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "active_options":
            self.resolve_interactive_selection(event.option.id)

    def on_key(self, event: events.Key) -> None:
        # Unified wrap-around navigation for whichever picker is active. The list never holds
        # focus (the input does), so Arrow keys bubble here; we drive the WrappingOptionList,
        # whose action_cursor_* wrap at both ends.
        if self._picker_on_select is None:
            return
        try:
            active_options = self.query_one("#active_options", WrappingOptionList)
        except Exception:
            return
        if event.key == "down":
            active_options.action_cursor_down()
            active_options.scroll_to_highlight()
            event.stop()
        elif event.key == "up":
            active_options.action_cursor_up()
            active_options.scroll_to_highlight()
            event.stop()
        elif event.key == "escape":
            self.resolve_interactive_selection(None)
            event.stop()

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

    def handle_sessions(self, query: str = "") -> None:
        from datetime import UTC, datetime

        from rich.text import Text

        from xhx_agent.runtime.session import format_session_line, format_session_meta, list_conversations
        # One entry per conversation (a multi-turn dialogue collapses to its latest, full transcript).
        conversations = list_conversations(self.workspace)
        if not conversations:
            self.append_message("system> No sessions recorded yet.")
            return

        recent = list(reversed(conversations))
        if query:
            q_lower = query.lower()
            recent = [c for c in recent if q_lower in c.task.lower()]
            if not recent:
                self.append_message(f"system> No sessions matching '{query}' found.")
                return

        now = datetime.now(UTC)
        lines = [format_session_line(entry, now) for entry in recent]
        self.append_message(f"system> {len(recent)} 个会话（↑↓ 选，Enter 恢复，Esc 开新）")
        self.set_detail("sessions", "\n".join(lines))

        options = []
        for entry in recent:
            task_single = " ".join(entry.task.splitlines())
            if len(task_single) > 60:
                task_single = task_single[:60] + "…"
            meta = format_session_meta(entry, now)
            prompt = Text.assemble((task_single, "bold"), "\n", (meta, "dim"))
            options.append((prompt, entry.run_id))

        self.present_picker(
            options,
            on_select=self._select_session,
            title=f"Resume session ({len(recent)})",
            placeholder="↑/↓ 选 · Enter 恢复 · Esc 开新会话 · /sessions <词> 过滤",
        )

    def handle_resume(self, run_id: str) -> None:
        if not run_id:
            # No id given: show the session picker (Arrow keys + Enter resumes directly).
            self.handle_sessions()
            return
        from xhx_agent.runtime.session import (
            list_sessions,
            load_session,
            load_transcript_messages,
            load_view_log,
            resolve_run_id,
        )

        entry = load_session(self.workspace, run_id)
        if not entry:
            resolved, cands = resolve_run_id(list_sessions(self.workspace), run_id)
            if resolved:
                entry = load_session(self.workspace, resolved)
            elif len(cands) > 1:
                self.append_message("system> 多个会话匹配，请补全：" + ", ".join(cands))
                return
            else:
                self.append_message(f"system> Session '{run_id}' not found.")
                return

        self.active_detail = "overview"
        self.detail_text = (
            "Use /plan, /context, /evidence, /diff, /verify, /repair, or /dashboard to inspect runtime state."
        )
        from xhx_agent.runtime.app import RunResult
        result = RunResult(
            run_id=entry.run_id,
            status=entry.status,
            changed_files=list(entry.changed_files),
            commands=[],
            verification=entry.verification,
            summary_path=entry.summary_path or "",
            risk_summary=[],
        )
        self.last_result = result
        self.state.apply_result(result)
        self.state.task = entry.task
        if entry.mode:
            self.state.mode = entry.mode
        # Continue the same conversation so further turns keep collapsing into one resume entry.
        self.conversation_id = entry.conversation_id or self.conversation_id

        messages = load_transcript_messages(self.workspace, entry.transcript_path)
        if messages:
            # Feed the real transcript to the model on the next turn (true memory), not just the UI.
            self.prior_messages = messages

        view = load_view_log(self.workspace, entry.view_path)
        if view is not None:
            self.messages = list(view)
        elif messages:
            self.messages.clear()
            for msg in messages:
                role = msg.get("role")
                content = msg.get("content")
                if role == "system":
                    continue
                if role == "user":
                    task_text = content
                    if task_text.startswith("Follow-up task in the same console session."):
                        if "\nUser request:\n" in task_text:
                            parts = task_text.split("\nUser request:\n", 1)
                            if len(parts) > 1:
                                subparts = parts[1].split("\n\nPrevious run context:", 1)
                                task_text = subparts[0].strip()
                    self.messages.append(f"user> {task_text}")
                elif role == "assistant":
                    if content:
                        self.messages.append(f"assistant> {content}")

        self.append_message("system> 已恢复会话（完整界面+记忆），直接提问即可继续")
        self.refresh_snapshot()

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
