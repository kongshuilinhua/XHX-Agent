"""会话管理命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_session(ctx: CommandContext) -> None:
    """恢复、新建或删除会话。

    - 无参数 / list / resume → 弹出可上下键选择的历史会话列表（选中即恢复）。
    - new → 新建空会话。
    - delete <id> → 删除指定历史会话的落盘文件。
    """
    raw = ctx.args.strip() if ctx.args else ""
    parts = raw.split(maxsplit=1)
    sub = parts[0].lower() if parts else ""

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

    if sub == "delete":
        if len(parts) < 2:
            ctx.ui.add_system_message("用法: /session delete <id>")
            return
        sid = parts[1].strip()
        sm = ctx.session_manager
        if sm is None:
            ctx.ui.add_system_message("会话管理器未初始化")
            return
        if sm.delete(sid):
            ctx.ui.add_system_message(f"已删除会话: {sid}")
        else:
            ctx.ui.add_system_message(f"未找到会话: {sid}")
        return

    if sub in ("", "list", "resume"):
        await ctx.ui.show_resume_picker()
        return

    ctx.ui.add_system_message("用法: /session [list|resume|new|delete <id>]")


SESSION_COMMAND = Command(
    name="session",
    description="恢复历史会话（上下键选择）、新建或删除会话",
    usage="/session [list|resume|new|delete <id>]",
    handler=handle_session,
)
