"""Worktree 命令。"""
from typing import Any


def create_worktree_command(manager: Any) -> Any:
    """创建 Worktree 管理命令，返回 Command 对象。"""
    from xhx_agent.commands import Command
    return Command(name="worktree", description="Manage git worktrees")

