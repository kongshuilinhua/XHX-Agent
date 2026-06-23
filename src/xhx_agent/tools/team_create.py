"""TeamCreate 工具。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class TeamCreateParams(BaseModel):
    name: str = ""
    description: str = ""


class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = (
        "Create a new team for multi-agent collaboration. "
        "创建后用 Agent 工具传 team_name 和 name 生成长期队友，队友之间用 SendMessage 通信。"
    )
    params_model = TeamCreateParams
    category = "command"
    is_system_tool = True

    def __init__(
        self,
        team_manager: Any = None,
        parent_agent: Any = None,
        teammate_mode: str = "",
        is_interactive: bool = True,
        enable_coordinator_mode: bool = False,
        manager: Any = None,
        **kwargs: Any,
    ) -> None:
        self._team_manager = team_manager or manager
        self._parent_agent = parent_agent
        self._teammate_mode = teammate_mode
        self._is_interactive = is_interactive
        self._enable_coordinator_mode = enable_coordinator_mode

    async def execute(self, params: TeamCreateParams) -> ToolResult:  # type: ignore[override]
        if self._team_manager is None or self._parent_agent is None:
            return ToolResult(output="TeamManager 或 parent agent 未配置。", is_error=True)
        if not params.name.strip():
            return ToolResult(output="团队名不能为空。", is_error=True)

        from xhx_agent.teams.spawn import detect_backend

        backend = detect_backend(self._teammate_mode, self._is_interactive)
        try:
            team = self._team_manager.create_team(
                name=params.name,
                lead_agent_id=self._parent_agent.agent_id,
                description=params.description,
            )
        except Exception as e:
            return ToolResult(output=f"创建团队失败: {e}", is_error=True)

        return ToolResult(
            output=(
                f"Team '{team.name}' created (backend: {backend.value}).\n"
                f"用 Agent 工具传 team_name='{team.name}' 和 name=<队友名> 生成队友。"
            )
        )
