"""权限模式命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext
from xhx_agent.permissions import PermissionMode

_MODE_LIST = [m.value for m in PermissionMode]


async def handle_permission(ctx: CommandContext) -> None:
    """查看或切换权限模式。"""
    if not ctx.args:
        if ctx.agent:
            current = ctx.agent.permission_mode
            ctx.ui.add_system_message(f"当前权限模式: {current.value if hasattr(current, 'value') else current}")
        ctx.ui.add_system_message(f"可用模式: {', '.join(_MODE_LIST)}")
        ctx.ui.add_system_message("用法: /permission <模式名>")
        return

    mode_name = ctx.args.strip().lower()
    try:
        new_mode = PermissionMode(mode_name)
    except ValueError:
        ctx.ui.add_system_message(f"未知模式: {mode_name}，可用: {', '.join(_MODE_LIST)}")
        return

    if ctx.agent:
        ctx.agent.permission_mode = new_mode
    ctx.ui.add_system_message(f"权限模式已切换为: {new_mode.value}")


PERMISSION_COMMAND = Command(
    name="permission",
    aliases=["perm"],
    description="查看或切换权限模式",
    usage="/permission [模式名]",
    arg_prompt="可选模式: default, accept_edits, plan, bypass, dont_ask",
    handler=handle_permission,
)
