"""队友进度树形展示 Widget。

来源：mewcode teammate_tree.py，适配 XHX-Agent TUI。
"""

from __future__ import annotations

from rich.text import Text
from textual.reactive import reactive
from textual.widget import Widget

from xhx_agent.teams.progress import TeammateProgress


class TeammateTree(Widget):
    """在 TUI 中渲染队友进度树。"""

    DEFAULT_CSS = """
    TeammateTree {
        height: auto;
        margin: 0 1;
    }
    """

    teammates: reactive[list[TeammateProgress]] = reactive(list, layout=True)
    leader_tokens: reactive[int] = reactive(0)

    def render(self) -> Text:
        if not self.teammates:
            return Text("")

        lines = Text()
        # Leader 行
        lines.append("  ┌─ ", style="dim")
        lines.append("lead", style="cyan")
        lines.append(": orchestrating", style="dim")
        if self.leader_tokens > 0:
            lines.append(
                f" · {TeammateProgress.format_tokens(self.leader_tokens)} tokens",
                style="dim",
            )
        lines.append("\n")

        for i, p in enumerate(self.teammates):
            is_last = i == len(self.teammates) - 1
            connector = "  └─ " if is_last else "  ├─ "

            lines.append(connector, style="dim")
            lines.append(f"@{p.name}", style="cyan")
            lines.append(": ")

            if p.status == "completed":
                lines.append("✓ done", style="green")
            elif p.status == "failed":
                lines.append("✗ failed", style="red")
            elif p.status == "stopped":
                lines.append("stopped", style="yellow")
            else:
                lines.append(f"{p.activity_summary}", style="dim")

            lines.append(
                f" · {p.tool_use_count} tools"
                f" · {p.format_tokens(p.token_count)} tokens",
                style="dim",
            )
            if not is_last:
                lines.append("\n")

        return lines
