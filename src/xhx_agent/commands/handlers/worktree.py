"""Worktree 命令。"""

from __future__ import annotations

from typing import Any


def create_worktree_command(manager: Any) -> Any:
    """创建 Worktree 管理命令，返回 Command 对象。"""
    from xhx_agent.commands import Command, CommandContext

    async def handle(ctx: CommandContext) -> None:
        if manager is None:
            ctx.ui.add_system_message("Worktree 管理器未初始化")
            return
        worktrees = manager.list_worktrees()
        if not worktrees:
            ctx.ui.add_system_message("当前没有活动的 worktree")
            return
        lines = ["活动 worktree："]
        for w in worktrees:
            name = getattr(w, "name", "?")
            branch = getattr(w, "branch", "?")
            path = getattr(w, "worktree_path", "?")
            lines.append(f"  {name}  [{branch}]  {path}")
        ctx.ui.add_system_message("\n".join(lines))

    return Command(
        name="worktree",
        description="列出 git worktree",
        usage="/worktree",
        handler=handle,
    )
