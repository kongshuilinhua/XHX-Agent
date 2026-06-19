"""Trace 命令。"""

from __future__ import annotations

from typing import Any


def create_trace_command(manager: Any, agent_id: str = "", **kwargs: Any) -> Any:
    from xhx_agent.commands import Command, CommandContext

    async def handle(ctx: CommandContext) -> None:
        if manager is None:
            ctx.ui.add_system_message("Trace 管理器未初始化")
            return
        nodes = list(getattr(manager, "_nodes", {}).values())
        if not nodes:
            ctx.ui.add_system_message("暂无 agent trace 记录")
            return
        lines = ["Agent trace："]
        for n in nodes:
            aid = getattr(n, "agent_id", "?")
            status = getattr(n, "status", "?")
            atype = getattr(n, "agent_type", "?")
            tin = getattr(n, "input_tokens", 0)
            tout = getattr(n, "output_tokens", 0)
            lines.append(f"  {str(aid)[:8]}  [{status}]  {atype}  in={tin} out={tout}")
        ctx.ui.add_system_message("\n".join(lines))

    return Command(
        name="trace",
        description="查看 agent trace",
        usage="/trace",
        handler=handle,
    )
