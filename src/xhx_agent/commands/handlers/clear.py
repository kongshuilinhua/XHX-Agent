"""清屏命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_clear(ctx: CommandContext) -> None:
    """清空聊天区域。"""
    clear_chat = ctx.config.get("clear_chat")
    if clear_chat:
        clear_chat()
    ctx.ui.add_system_message("聊天已清空")


CLEAR_COMMAND = Command(
    name="clear",
    description="清空聊天区域",
    usage="/clear",
    handler=handle_clear,
)
