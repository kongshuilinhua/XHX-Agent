"""LoadSkill 工具 — 动态加载技能。"""
from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class LoadSkillParams(BaseModel):
    skill: str = ""


class LoadSkill(Tool):
    name = "LoadSkill"
    description = "Load a skill by name to make its tools available."
    params_model = LoadSkillParams
    category = "read"
    is_system_tool = True

    def __init__(self, loader: Any = None, **kwargs: Any) -> None:
        self._loader = loader
        self._agent: Any = None

    def set_loader(self, loader: Any) -> None:
        self._loader = loader

    def set_agent(self, agent: Any) -> None:
        self._agent = agent

    async def execute(self, params: LoadSkillParams) -> ToolResult:
        if self._loader:
            skill = self._loader.get(params.skill)
            if skill:
                return ToolResult(output=f"Loaded skill: {params.skill}")
            return ToolResult(
                output=f"Skill not found: {params.skill}", is_error=True
            )
        return ToolResult(output="Skill loader not configured.", is_error=True)
