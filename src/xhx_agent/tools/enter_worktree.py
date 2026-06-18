"""EnterWorktree 工具。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class EnterWorktreeParams(BaseModel):
    name: str = ""
    path: str = ""


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = "Enter a git worktree for isolated development."
    params_model = EnterWorktreeParams
    category = "command"

    def __init__(self, manager: Any = None, worktree_manager: Any = None, **kwargs: Any) -> None:
        self._manager = manager or worktree_manager

    async def execute(self, params: EnterWorktreeParams) -> ToolResult:
        if self._manager:
            try:
                wt = await self._manager.create(params.name or params.path)
                return ToolResult(output=f"Entered worktree: {wt.path}")
            except Exception as e:
                return ToolResult(output=str(e), is_error=True)
        return ToolResult(output="Worktree manager not configured.", is_error=True)
