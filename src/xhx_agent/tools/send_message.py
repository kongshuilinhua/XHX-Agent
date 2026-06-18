"""SendMessage 工具 — 团队内部消息。"""
from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.tools.base import Tool, ToolResult


class SendMessageParams(BaseModel):
    to: str = ""
    content: str = ""


class SendMessageTool(Tool):
    name = "SendMessage"
    description = "Send a message to a teammate."
    params_model = SendMessageParams
    category = "command"

    async def execute(self, params: SendMessageParams) -> ToolResult:
        return ToolResult(output=f"Message sent to {params.to}.")
