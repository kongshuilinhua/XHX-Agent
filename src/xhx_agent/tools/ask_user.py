"""AskUser 工具 — 交互式用户询问。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class AskUserParams(BaseModel):
    questions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class AskUserEvent:
    questions: list[dict[str, Any]] = field(default_factory=list)
    future: Any = None


class AskUserTool(Tool):
    name = "AskUserQuestion"
    description = "Ask the user one or more questions to clarify requirements."
    params_model = AskUserParams
    category = "read"
    is_system_tool = True

    def __init__(self, callback: Any = None, **kwargs: Any) -> None:
        self._callback = callback

    async def execute(self, params: AskUserParams) -> ToolResult:
        if self._callback:
            event = AskUserEvent(questions=params.questions)
            result = await self._callback(event)
            return ToolResult(output=str(result))
        return ToolResult(output="AskUser not configured.", is_error=True)
