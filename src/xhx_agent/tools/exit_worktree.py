"""ExitWorktree 工具。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class ExitWorktreeParams(BaseModel):
    action: str = "keep"


class ExitWorktreeTool(Tool):
    name = "ExitWorktree"
    description = "Exit current git worktree."
    params_model = ExitWorktreeParams
    category = "command"

    def __init__(self, manager: Any = None, worktree_manager: Any = None, **kwargs: Any) -> None:
        self._manager = manager or worktree_manager

    async def execute(self, params: ExitWorktreeParams) -> ToolResult:
        return ToolResult(output="Exited worktree.")
