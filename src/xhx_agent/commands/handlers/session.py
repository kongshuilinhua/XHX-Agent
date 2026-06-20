"""会话管理命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_session(ctx: CommandContext) -> None:
    """恢复或新建会话。

    - 无参数 / list / resume → 弹出可上下键选择的历史会话列表（选中即恢复）。
    - new → 新建空会话。
    """
    sub = ctx.args.strip().lower() if ctx.args else ""

    if sub == "new":
        set_session = ctx.config.get("set_session")
        set_conversation = ctx.config.get("set_conversation")
        clear_chat = ctx.config.get("clear_chat")
        if set_session:
            set_session(None)
        if set_conversation:
            set_conversation(None)
        if clear_chat:
            clear_chat()
        ctx.ui.add_system_message("已创建新会话")
        return

    if sub in ("", "list", "resume"):
        await ctx.ui.show_resume_picker()
        return

    ctx.ui.add_system_message("用法: /session [list|resume|new]")


SESSION_COMMAND = Command(
    name="session",
    description="恢复历史会话（上下键选择）或新建会话",
    usage="/session [list|resume|new]",
    handler=handle_session,
)
