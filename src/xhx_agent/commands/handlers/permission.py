"""权限模式命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext
from xhx_agent.permissions import PermissionMode

# 展示用：枚举名（小写）→ 实际值。值是 camelCase（acceptEdits/bypassPermissions/dontAsk），
# 用户很难手敲对，所以这里做容错解析（同时接受枚举名、值、常见别名，且不区分大小写）。
_VALID_HINT = ", ".join(f"{m.name.lower()}（{m.value}）" for m in PermissionMode)

_ALIASES = {
    "accept": PermissionMode.ACCEPT_EDITS,
    "edits": PermissionMode.ACCEPT_EDITS,
    "auto": PermissionMode.AUTO,
    "bypass": PermissionMode.BYPASS,
    "yolo": PermissionMode.BYPASS,
}


def _resolve_mode(token: str) -> PermissionMode | None:
    """容错解析模式：枚举名 / 值 / 别名，均不区分大小写，并忽略下划线。"""
    t = token.strip().lower().replace("_", "").replace("-", "")
    for m in PermissionMode:
        if t in (m.name.lower().replace("_", ""), m.value.lower()):
            return m
    return _ALIASES.get(t)


async def handle_permission(ctx: CommandContext) -> None:
    """查看或切换权限模式。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    if not ctx.args:
        current = ctx.agent.permission_mode
        ctx.ui.add_system_message(f"当前权限模式: {current.value if hasattr(current, 'value') else current}")
        ctx.ui.add_system_message(f"可用模式: {_VALID_HINT}")
        ctx.ui.add_system_message("用法: /permission <模式名>")
        return

    new_mode = _resolve_mode(ctx.args)
    if new_mode is None:
        ctx.ui.add_system_message(f"未知模式: {ctx.args.strip()}，可用: {_VALID_HINT}")
        return

    # plan 模式走 set_plan_mode（保存 _pre_plan_mode、收紧工具、刷新标签）；其余用
    # agent.set_permission_mode（它会同步更新 permission_checker.mode——直接赋值
    # agent.permission_mode 不会，导致真正的权限闸门用的还是旧模式）。
    if new_mode == PermissionMode.PLAN:
        ctx.ui.set_plan_mode(True)
    else:
        ctx.agent.set_permission_mode(new_mode)
        ctx.ui.refresh_status()
    ctx.ui.add_system_message(f"权限模式已切换为: {new_mode.value}")


PERMISSION_COMMAND = Command(
    name="permission",
    description="查看或切换权限模式",
    usage="/permission [模式名]",
    handler=handle_permission,
)
