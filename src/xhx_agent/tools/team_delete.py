"""TeamDelete 工具。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class TeamDeleteParams(BaseModel):
    name: str = ""


class TeamDeleteTool(Tool):
    name = "TeamDelete"
    description = "Delete a team and clean up its members, mailbox and worktrees."
    params_model = TeamDeleteParams
    category = "command"
    is_system_tool = True

    def __init__(
        self,
        team_manager: Any = None,
        parent_agent: Any = None,
        manager: Any = None,
        **kwargs: Any,
    ) -> None:
        self._team_manager = team_manager or manager

    async def execute(self, params: TeamDeleteParams) -> ToolResult:  # type: ignore[override]
        if self._team_manager is None:
            return ToolResult(output="TeamManager 未配置。", is_error=True)
        if not params.name.strip():
            return ToolResult(output="团队名不能为空。", is_error=True)

        team = self._team_manager.get_team(params.name)
        if team is None:
            return ToolResult(output=f"Team '{params.name}' not found.", is_error=True)

        self._team_manager.delete_team(params.name)
        return ToolResult(output=f"Team '{params.name}' deleted.")
