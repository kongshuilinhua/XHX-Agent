"""任务管理命令。"""

from __future__ import annotations

from typing import Any


def create_tasks_command(manager: Any, **kwargs: Any) -> Any:
    from xhx_agent.commands import Command, CommandContext

    async def handle(ctx: CommandContext) -> None:
        if manager is None:
            ctx.ui.add_system_message("任务管理器未初始化")
            return
        tasks = manager.list_tasks()
        if not tasks:
            ctx.ui.add_system_message("当前没有后台任务")
            return
        lines = ["后台任务："]
        for t in tasks:
            tid = getattr(t, "id", "?")
            status = getattr(t, "status", "?")
            name = getattr(t, "name", "")
            lines.append(f"  {str(tid)[:8]}  [{status}]  {name}")
        ctx.ui.add_system_message("\n".join(lines))

    return Command(
        name="tasks",
        description="列出后台任务",
        usage="/tasks",
        handler=handle,
    )
