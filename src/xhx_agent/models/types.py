from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ToolStep(BaseModel):
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)


class ModelPlan(BaseModel):
    summary: str
    status: Literal["continue", "done"] = "continue"
    steps: list[ToolStep]

    @field_validator("steps")
    @classmethod
    def require_steps(cls, value: list[ToolStep]) -> list[ToolStep]:
        if value is None:
            raise ValueError("Model plan steps must be a list.")
        return value

    @field_validator("steps")
    @classmethod
    def require_steps_unless_done(cls, value: list[ToolStep], info) -> list[ToolStep]:
        status = info.data.get("status", "continue")
        if status != "done" and not value:
            raise ValueError("Model plan must include at least one tool step.")
        return value


MockPlan = ModelPlan


class ModelClientError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ChatResult(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
