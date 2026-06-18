"""会话管理命令。"""
from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_session(ctx: CommandContext) -> None:
    """查看或管理会话。"""
    if ctx.session_manager is None:
        ctx.ui.add_system_message("会话管理器未初始化")
        return

    sub = ctx.args.strip().lower() if ctx.args else "list"

    if sub == "list" or sub == "":
        sessions = ctx.session_manager.list_sessions()
        if not sessions:
            ctx.ui.add_system_message("暂无历史会话")
            return
        lines = ["历史会话："]
        for s in sessions[-20:]:  # 最近 20 个
            sid = getattr(s, "run_id", str(s))
            task = getattr(s, "task", "")[:60] or ""
            lines.append(f"  {sid}  {task}")
        ctx.ui.add_system_message("\n".join(lines))
        return

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

    ctx.ui.add_system_message("用法: /session [list|new]")


SESSION_COMMAND = Command(
    name="session",
    aliases=["sess"],
    description="查看或管理会话",
    usage="/session [list|new]",
    handler=handle_session,
)
