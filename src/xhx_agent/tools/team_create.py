"""TeamCreate 工具。"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class TeamCreateParams(BaseModel):
    name: str = ""
    description: str = ""


class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = "Create a new team for multi-agent collaboration."
    params_model = TeamCreateParams
    category = "command"
    is_system_tool = True

    def __init__(self, manager: Any = None, **kwargs: Any) -> None:
        self._manager = manager

    async def execute(self, params: TeamCreateParams) -> ToolResult:
        return ToolResult(output=f"Team '{params.name}' created.")
