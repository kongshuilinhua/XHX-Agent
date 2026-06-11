from __future__ import annotations

from rich import box
from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from xhx_agent.tui.state import ConsoleState

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
    "/exit",
]


def render_console_page(
    state: ConsoleState, *, workspace: str, profile: str, auto_repair: bool, assume_yes: bool
) -> Panel:
    """Render the v0.5 terminal page without owning input or execution."""

    header = _header_table(state, workspace=workspace, profile=profile, auto_repair=auto_repair, assume_yes=assume_yes)
    body = Columns(
        [
            _conversation_panel(state),
            _side_panel(state),
        ],
        equal=False,
        expand=True,
    )
    footer = _footer_panel()
    return Panel(Group(header, body, footer), title="xhx-agent", border_style="cyan")


def _header_table(
    state: ConsoleState, *, workspace: str, profile: str, auto_repair: bool, assume_yes: bool
) -> RenderableType:
    table = Table.grid(expand=True)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    table.add_column(ratio=1)
    run_id = state.run_id or "none"
    flags = []
    if auto_repair:
        flags.append("repair:on")
    if assume_yes:
        flags.append("yes:on")
    table.add_row(
        f"state: {state.status}",
        f"profile: {profile}",
        f"run: {run_id}",
    )
    table.add_row(
        f"mode: {state.mode}",
        f"verification: {state.verification}",
        f"flags: {', '.join(flags) or 'none'}",
    )
    # workspace path can be long; a 1/3-width grid cell overflowed into the next
    # column (rendered "…XHX-Agentcancel: no"). Put it on its own full-width line
    # that folds on narrow terminals instead of bleeding into the cancel field.
    cancel = "yes" if state.cancel_requested else "no"
    workspace_line = Text(f"workspace: {workspace}    cancel: {cancel}", overflow="fold")
    return Group(table, workspace_line)


def _conversation_panel(state: ConsoleState) -> Panel:
    rows: list[RenderableType] = []
    if state.task:
        rows.append(Text(f"user> {state.task}", style="bold"))
    if state.plan_summary:
        rows.append(Text(f"plan> {state.plan_summary}"))
    if state.model_output:
        rows.append(Text(f"model> {_compact_model_output(state.model_output)}", style="cyan"))
    if state.tools:
        rows.append(_activity_table(state))
    else:
        rows.append(Text("No tool activity yet.", style="dim"))
    if state.summary_path:
        rows.append(Text(f"summary> {state.summary_path}", style="green"))
    if state.cancel_requested:
        rows.append(Text(f"cancel> {state.cancel_reason or 'requested'}", style="yellow"))
    return Panel(Group(*rows), title="Conversation", border_style="blue")


def _side_panel(state: ConsoleState) -> Panel:
    return Panel(
        Group(
            _context_table(state),
            _changed_files_table(state),
            _events_table(state),
        ),
        title="Runtime State",
        border_style="magenta",
    )


def _activity_table(state: ConsoleState) -> Table:
    table = Table(title="Activity", box=box.SIMPLE_HEAVY)
    table.add_column("Type")
    table.add_column("Status")
    table.add_column("Summary")
    for item in state.tools[-5:]:
        table.add_row(f"tool:{item.tool}", item.status, item.summary or "")
    for vitem in state.verifications[-3:]:
        exit_code = "none" if vitem.exit_code is None else str(vitem.exit_code)
        table.add_row("verify", vitem.status, f"{vitem.command} exit_code={exit_code}")
    if state.repair_attempts:
        table.add_row(
            "repair",
            f"{state.repair_attempts}/{state.repair_max_attempts or '?'}",
            state.repair_reason,
        )
    return table


def _context_table(state: ConsoleState) -> Table:
    table = Table(title="Context", box=box.SIMPLE)
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("turn", str(state.context_turn or "none"))
    table.add_row("selected", str(state.context_selected))
    table.add_row("omitted", str(state.context_omitted))
    budget = (
        f"{state.context_used_tokens_estimate}/{state.context_budget_tokens}" if state.context_budget_tokens else "none"
    )
    table.add_row("budget", budget)
    table.add_row("languages", ", ".join(state.detected_languages) or "unknown")
    table.add_row("files", str(state.file_count))
    table.add_row("model_deltas", str(state.model_delta_count))
    return table


def _changed_files_table(state: ConsoleState) -> Table:
    table = Table(title="Changed Files", box=box.SIMPLE)
    table.add_column("Path")
    for path in state.changed_files[:8]:
        table.add_row(path)
    if len(state.changed_files) > 8:
        table.add_row(f"... {len(state.changed_files) - 8} more")
    if not state.changed_files:
        table.add_row("none")
    return table


def _events_table(state: ConsoleState) -> Table:
    table = Table(title="Events", box=box.SIMPLE)
    table.add_column("Type")
    table.add_column("Message")
    for event in state.events[-5:]:
        table.add_row(event.type, event.message)
    if not state.events:
        table.add_row("none", "No events yet.")
    return table


def _footer_panel() -> Panel:
    hints = "  ".join(SLASH_COMMAND_HINTS)
    return Panel(Text(hints, overflow="fold"), title="Commands", border_style="green")


def _compact_model_output(text: str, limit: int = 600) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return "..." + normalized[-limit:]
