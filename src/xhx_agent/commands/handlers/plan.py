"""Plan 模式命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_plan(ctx: CommandContext) -> None:
    """进入或退出 Plan 模式（只读计划，需确认才执行）。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    if ctx.agent.plan_mode:
        # 退出 plan 模式：委托 TUI 的 set_plan_mode(False) 以恢复 _pre_plan_mode
        ctx.ui.set_plan_mode(False)
        ctx.ui.add_system_message("已退出 Plan 模式")
    else:
        # 进入 plan 模式：委托 TUI 的 set_plan_mode(True) 以保存当前模式
        ctx.ui.set_plan_mode(True)
        ctx.ui.add_system_message("已进入 Plan 模式（计划为只读，须确认后才执行）")


PLAN_COMMAND = Command(
    name="plan",
    description="切换 Plan 模式（只读计划 → 确认后执行）",
    usage="/plan",
    handler=handle_plan,
)
