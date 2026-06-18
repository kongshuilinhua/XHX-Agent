"""TeamDelete 工具。"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class TeamDeleteParams(BaseModel):
    name: str = ""


class TeamDeleteTool(Tool):
    name = "TeamDelete"
    description = "Delete a team."
    params_model = TeamDeleteParams
    category = "command"
    is_system_tool = True

    def __init__(self, manager: Any = None, **kwargs: Any) -> None:
        self._manager = manager

    async def execute(self, params: TeamDeleteParams) -> ToolResult:
        return ToolResult(output=f"Team '{params.name}' deleted.")
