"""Plan 模式命令。"""
from __future__ import annotations

from xhx_agent.commands import Command, CommandContext
from xhx_agent.permissions import PermissionMode


async def handle_plan(ctx: CommandContext) -> None:
    """进入或退出 Plan 模式（只读计划，需确认才执行）。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    if ctx.agent.plan_mode:
        ctx.agent.permission_mode = PermissionMode.DEFAULT
        ctx.ui.add_system_message("已退出 Plan 模式，当前为 default 模式")
    else:
        ctx.agent.permission_mode = PermissionMode.PLAN
        ctx.ui.add_system_message("已进入 Plan 模式（计划为只读，须确认后才执行）")


PLAN_COMMAND = Command(
    name="plan",
    aliases=["p"],
    description="切换 Plan 模式（只读计划 → 确认后执行）",
    usage="/plan",
    handler=handle_plan,
)
