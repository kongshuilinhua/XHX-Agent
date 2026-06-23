"""会话记忆管理命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_memory(ctx: CommandContext) -> None:
    """查看或管理长期记忆。"""
    if not ctx.memory_manager:
        ctx.ui.add_system_message("记忆管理器未初始化")
        return

    sub = ctx.args.strip().lower() if ctx.args else "list"

    if sub == "list" or sub == "":
        content = ctx.memory_manager.load()
        if not content or not content.strip():
            ctx.ui.add_system_message("暂无记忆")
            return
        mem_lines = [line for line in content.split("\n") if line.strip().startswith("- ")]
        if not mem_lines:
            ctx.ui.add_system_message("暂无结构化记忆条目")
            return
        ctx.ui.add_system_message("记忆条目：\n" + "\n".join(mem_lines))
        return

    if sub == "clear":
        cleared = ctx.memory_manager.clear()
        ctx.ui.add_system_message("记忆已清空" if cleared else "暂无可清空的记忆")
        return

    if sub == "edit":
        mm = ctx.memory_manager
        ctx.ui.add_system_message(f"编辑记忆文件：\n  用户级: {mm.user_path}\n  项目级: {mm.project_path}")
        return

    ctx.ui.add_system_message("用法: /memory [list|clear|edit]")


MEMORY_COMMAND = Command(
    name="memory",
    description="查看或管理长期记忆",
    usage="/memory [list|clear|edit]",
    handler=handle_memory,
)
