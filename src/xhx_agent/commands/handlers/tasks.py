"""任务管理命令。"""

from __future__ import annotations

import time
from typing import Any


def _elapsed(start: float, end: float | None) -> str:
    e = (end or time.monotonic()) - start
    return f"{e / 60:.1f}m" if e >= 60 else f"{e:.0f}s"


def create_tasks_command(manager: Any, **kwargs: Any) -> Any:
    from xhx_agent.commands import Command, CommandContext

    async def handle(ctx: CommandContext) -> None:
        if manager is None:
            ctx.ui.add_system_message("任务管理器未初始化")
            return

        parts = ctx.args.split(maxsplit=1) if ctx.args else []
        sub = parts[0] if parts else ""

        if sub == "info":
            if len(parts) < 2:
                ctx.ui.add_system_message("用法: /tasks info <task-id>")
                return
            tid = parts[1].strip()
            bg = manager.get(tid)
            if bg is None:
                ctx.ui.add_system_message(f"未找到任务: {tid}")
                return
            lines = [
                f"任务详情: {bg.id}",
                f"  名称: {bg.name}",
                f"  状态: {bg.status}",
                f"  耗时: {_elapsed(bg.start_time, bg.end_time)}",
            ]
            if bg.result:
                preview = bg.result[:2000] + ("\n... (truncated)" if len(bg.result) > 2000 else "")
                lines.append(f"  结果:\n{preview}")
            ctx.ui.add_system_message("\n".join(lines))
            return

        if sub == "cancel":
            if len(parts) < 2:
                ctx.ui.add_system_message("用法: /tasks cancel <task-id>")
                return
            tid = parts[1].strip()
            if manager.cancel(tid):
                ctx.ui.add_system_message(f"已取消任务: {tid}")
            else:
                ctx.ui.add_system_message(f"无法取消任务: {tid}（可能不存在或已完成）")
            return

        # 默认：列出全部后台任务
        tasks = manager.list_tasks()
        if not tasks:
            ctx.ui.add_system_message("当前没有后台任务")
            return
        lines = ["后台任务："]
        for t in tasks:
            lines.append(f"  {str(t.id)[:8]}  [{t.status}]  {t.name}  {_elapsed(t.start_time, t.end_time)}")
        ctx.ui.add_system_message("\n".join(lines))

    return Command(
        name="tasks",
        description="管理后台任务（list/info/cancel）",
        usage="/tasks [info|cancel] [task-id]",
        handler=handle,
    )
