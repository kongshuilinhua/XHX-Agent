from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static

from xhx_agent.runtime.app import RunResult, RuntimeApp
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.page import SLASH_COMMAND_HINTS
from xhx_agent.tui.state import ConsoleState


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
        runtime_state = "\n".join(
            [
                f"workspace: {workspace}",
                f"mode: {state.mode}",
                f"verification: {state.verification}",
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
        self.messages: list[str] = []
        self.exit_requested = False
        self.widgets_ready = False

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
        should_continue = self.handle_text_input(event.value)
        self.refresh_snapshot()
        if not should_continue:
            self.exit()

    def handle_text_input(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return True
        if stripped.startswith("/"):
            return self.handle_slash_command(stripped)
        self.run_task(stripped)
        return True

    def run_task(self, task: str) -> None:
        self.messages.append(f"user> {task}")
        result = self.runtime.run_task(
            task,
            profile_name=self.profile,
            assume_yes=self.assume_yes,
            confirm_callback=self.confirm_terminal_command,
            auto_repair=self.auto_repair,
            event_callback=self.handle_runtime_event,
        )
        self.last_result = result
        self.state.apply_result(result)
        self.messages.append(f"system> run finished: {result.status}, verification: {result.verification}")

    def handle_runtime_event(self, event: RuntimeEvent) -> None:
        self.state.reduce(event)
        self.refresh_snapshot()

    def confirm_terminal_command(self, command: str, decision: PolicyDecision) -> bool:
        self.messages.append(
            "system> permission required in fullscreen mode; "
            f"declined by default: {command} ({decision.risk.value})"
        )
        return False

    def handle_slash_command(self, command_line: str) -> bool:
        command, _, _argument = command_line.partition(" ")
        if command == "/exit":
            self.exit_requested = True
            return False
        if command == "/clear":
            self.action_clear()
            return True
        if command == "/help":
            self.messages.append("system> available commands: /help /status /clear /exit")
            return True
        if command == "/status":
            self.messages.append(
                "system> "
                f"status: {self.state.status}; "
                f"verification: {self.state.verification}; "
                f"profile: {self.profile}; "
                f"changed_files: {len(self.state.changed_files)}"
            )
            return True
        self.messages.append(f"system> Unknown command: {command}")
        return True


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
