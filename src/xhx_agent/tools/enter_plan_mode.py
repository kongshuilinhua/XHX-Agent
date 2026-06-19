"""EnterPlanMode 工具 —— 模型自主进入 plan 模式。

对标 Claude Code 的设计哲学：进入 plan 模式是**安全操作、无需审批**——它只是把权限收紧到
只读（仍可读/搜索、派只读调研子 agent、问用户、写 plan 文件），剥夺了写改能力。真正的安全
边界是 ExitPlanMode（"计划好了，让我写代码"），那一步才需要用户审批。

这样模型就能**自主判断**"这个任务需要先规划"并主动进入 plan 模式，而不是只能等用户手动切。
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class EnterPlanModeParams(BaseModel):
    pass


class EnterPlanModeTool(Tool):
    name = "EnterPlanMode"
    description = (
        "Enter plan mode to research and design before making any changes. Call this "
        "autonomously when a task is complex enough to warrant planning first — e.g. new "
        "projects, multi-file features, or architectural changes. Entering is SAFE and needs "
        "no approval: it only restricts you to read-only (you can still read/search files, "
        "spawn read-only research sub-agents, ask the user, and write your plan file). When "
        "your plan is ready, call ExitPlanMode (or present_plan) to submit it for user approval."
    )
    params_model = EnterPlanModeParams
    category = "read"
    is_system_tool = True

    def __init__(
        self,
        on_enter: Callable[[], None] | None = None,
        is_plan_mode: Callable[[], bool] | None = None,
    ) -> None:
        self._on_enter = on_enter
        self._is_plan_mode = is_plan_mode

    async def execute(self, params: EnterPlanModeParams) -> ToolResult:  # type: ignore[override]
        if self._is_plan_mode is not None and self._is_plan_mode():
            return ToolResult(output="Already in plan mode. Keep researching, write your plan, then call ExitPlanMode.")
        if self._on_enter is not None:
            self._on_enter()
        return ToolResult(
            output=(
                "Entered plan mode (read-only). Research the codebase and design your approach, "
                "write the plan, then call ExitPlanMode to present it for approval. "
                "You cannot make any changes until the user approves."
            )
        )
