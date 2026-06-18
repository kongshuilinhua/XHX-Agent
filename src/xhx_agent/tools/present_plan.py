from __future__ import annotations

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult


class Params(BaseModel):
    plan: str = Field(description="拟定好的技术实现计划描述。")
    files_to_change: list[str] = Field(
        default_factory=list,
        description="计划要修改的文件路径列表。",
    )


class PresentPlanTool(Tool):
    name = "present_plan"
    description = "提交最终设计规划给用户进行确认。提交后将进入两段式的执行确认环节。"
    params_model = Params
    category = "read"

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(
            output=f"实现计划已成功呈报，等待用户核准...\n\n计划涉及文件: {', '.join(params.files_to_change) if params.files_to_change else '(无)'}"
        )
