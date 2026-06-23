"""PresentPlan 工具 —— 模型提交计划并触发审批对话框。

这是 Plan 模式两段式的"闸门"：模型写完 plan file 后调用本工具，
TUI 据此弹出审批对话框（YOLO / 手动审批 / 反馈修改）。
"""

from __future__ import annotations

from collections.abc import Callable

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
    description = (
        "Submit your implementation plan for user approval. This is a TOOL CALL -- "
        "invoking this tool is the only way to present a plan; never write the plan or "
        "an approval UI (checkboxes / 'Execute this plan' options) as plain text. "
        "Call this when your plan is complete and written to the plan file. "
        "The user will see an approval dialog and choose how to proceed."
    )
    params_model = Params
    category = "read"

    def __init__(
        self,
        is_plan_mode: Callable[[], bool] | None = None,
        plan_exists: Callable[[], bool] | None = None,
    ) -> None:
        self._is_plan_mode = is_plan_mode
        self._plan_exists = plan_exists
        # 模型成功调用本工具后置位；TUI 据此弹审批框。
        self._exit_requested = False
        # 本工具自带方案正文（审批框直接展示，不依赖 plan 文件）。
        self._plan_text = ""
        self._files_to_change: list[str] = []

    async def execute(self, params: Params) -> ToolResult:  # type: ignore[override]
        # 任何模式都可提交方案待审批（对标 Claude：模型自行判断任务复杂、主动提方案）。
        # 不再因"不在 plan 模式 / 无 plan 文件"报错——方案正文随参数带来即可。
        self._plan_text = params.plan or ""
        self._files_to_change = list(params.files_to_change or [])
        self._exit_requested = True
        return ToolResult(
            output=(
                "Plan submitted successfully. The user will now review and approve it. "
                "Do not call any more tools — end your turn now."
            )
        )
