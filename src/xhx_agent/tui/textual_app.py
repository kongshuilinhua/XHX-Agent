from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Static

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
    ) -> None:
        super().__init__()
        self.workspace = (workspace or Path.cwd()).resolve()
        self.profile = profile
        self.auto_repair = auto_repair
        self.assume_yes = assume_yes
        self.state = state or ConsoleState()

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
        self.refresh_snapshot()

    def refresh_snapshot(self) -> None:
        snapshot = TextualSnapshot.from_state(
            self.state,
            workspace=str(self.workspace),
            profile=self.profile,
            auto_repair=self.auto_repair,
            assume_yes=self.assume_yes,
        )
        self.title = snapshot.header
        self.query_one("#conversation", Static).update(snapshot.conversation)
        self.query_one("#runtime", Static).update(snapshot.runtime_state)
        self.query_one("#changed", Static).update("changed files:\n" + snapshot.changed_files)
        self.query_one("#commands", Static).update(snapshot.commands)

    def action_clear(self) -> None:
        self.state = ConsoleState()
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
