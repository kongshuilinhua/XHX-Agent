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

        parts = ctx.args.split() if ctx.args else []
        sub = parts[0] if parts else "list"
        rest = parts[1:]

        if sub == "list":
            worktrees = manager.list_worktrees()
            if not worktrees:
                ctx.ui.add_system_message("当前没有活动的 worktree")
                return
            lines = ["活动 worktree："]
            for w in worktrees:
                lines.append(f"  {w.name}  [{w.branch}]  {w.worktree_path}")
            ctx.ui.add_system_message("\n".join(lines))

        elif sub == "create":
            if not rest:
                ctx.ui.add_system_message("用法: /worktree create <name> [base-ref]")
                return
            name = rest[0]
            base = rest[1] if len(rest) > 1 else "HEAD"
            try:
                wt = await manager.create(name, base)
            except Exception as e:
                ctx.ui.add_system_message(f"创建 worktree 失败: {e}")
                return
            if ctx.agent:
                ctx.agent.work_dir = str(wt.path)
            ctx.ui.add_system_message(
                f"已创建并进入 worktree: {wt.name}\n  路径: {wt.worktree_path}\n  分支: {wt.branch}"
            )

        elif sub == "enter":
            if not rest:
                ctx.ui.add_system_message("用法: /worktree enter <name>")
                return
            try:
                wt = await manager.enter(rest[0])
            except Exception as e:
                ctx.ui.add_system_message(f"进入 worktree 失败: {e}")
                return
            if ctx.agent:
                ctx.agent.work_dir = str(wt.path)
            ctx.ui.add_system_message(f"已进入 worktree: {wt.name}\n  路径: {wt.worktree_path}")

        elif sub == "exit":
            worktrees = manager.list_worktrees()
            if not worktrees:
                ctx.ui.add_system_message("当前不在任何 worktree 中")
                return
            handle = worktrees[-1]
            action = "remove" if "--remove" in rest else "keep"
            discard = "--discard" in rest
            try:
                await manager.exit(handle.name, action=action, discard_changes=discard)
            except Exception as e:
                ctx.ui.add_system_message(f"退出 worktree 失败: {e}")
                return
            if ctx.agent:
                ctx.agent.work_dir = str(manager.project_root)
            msg = f"已退出 worktree: {handle.name}" + ("（已删除）" if action == "remove" else "")
            ctx.ui.add_system_message(msg)

        elif sub == "status":
            worktrees = manager.list_worktrees()
            if not worktrees:
                ctx.ui.add_system_message("当前不在任何 worktree 中")
                return
            handle = worktrees[-1]
            ctx.ui.add_system_message(
                f"Worktree 状态：\n  名称: {handle.name}\n  路径: {handle.worktree_path}\n  分支: {handle.branch}"
            )

        else:
            ctx.ui.add_system_message("用法: /worktree [list|create <name>|enter <name>|exit [--remove]|status]")

    return Command(
        name="worktree",
        description="管理 git worktree（list/create/enter/exit/status）",
        usage="/worktree [list|create <name>|enter <name>|exit [--remove]|status]",
        handler=handle,
    )
