"""ExitWorktree 工具。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class ExitWorktreeParams(BaseModel):
    action: str = "keep"
    discard_changes: bool = False


class ExitWorktreeTool(Tool):
    name = "ExitWorktree"
    description = (
        'Exit the current git worktree. action="keep" 保留 worktree 和分支；'
        'action="remove" 删除两者（worktree 有未提交改动/未合并提交时需 discard_changes=true 才会删）。'
    )
    params_model = ExitWorktreeParams
    category = "command"

    def __init__(self, manager: Any = None, worktree_manager: Any = None, **kwargs: Any) -> None:
        self._manager = manager or worktree_manager

    async def execute(self, params: ExitWorktreeParams) -> ToolResult:  # type: ignore[override]
        if self._manager is None:
            return ToolResult(output="Worktree manager not configured.", is_error=True)

        active = self._manager.list_worktrees()
        if not active:
            return ToolResult(
                output="No-op: 当前没有活动的 worktree 会话可退出。",
                is_error=True,
            )

        # restore_session 占位返回 None，所以以最近进入的 active worktree 作为"当前"。
        handle = active[-1]
        action = params.action if params.action in ("keep", "remove") else "keep"

        # remove 且未确认丢弃时，先检查未提交改动/未合并提交，避免误删丢工作。
        if action == "remove" and not params.discard_changes:
            from xhx_agent.worktree.changes import count_worktree_changes

            changes = count_worktree_changes(str(handle.path), handle.head_commit)
            if changes.uncommitted > 0 or changes.new_commits > 0:
                parts = []
                if changes.uncommitted > 0:
                    parts.append(f"{changes.uncommitted} 个未提交文件")
                if changes.new_commits > 0:
                    parts.append(f"{changes.new_commits} 个未合并提交")
                return ToolResult(
                    output=(
                        f"worktree '{handle.name}' 有 {'、'.join(parts)}，remove 会永久丢弃。"
                        '请与用户确认后用 discard_changes=true 重试，或用 action="keep" 保留。'
                    ),
                    is_error=True,
                )

        try:
            await self._manager.exit(handle.name, action=action, discard_changes=params.discard_changes)
        except Exception as e:
            return ToolResult(output=f"退出 worktree 失败: {e}", is_error=True)

        if action == "remove":
            return ToolResult(output=f"已退出并删除 worktree：{handle.worktree_path}")
        return ToolResult(output=f"已退出 worktree，工作保留在：{handle.worktree_path}")
