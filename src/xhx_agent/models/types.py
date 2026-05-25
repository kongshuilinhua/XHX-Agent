from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ToolStep(BaseModel):
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)


class ModelPlan(BaseModel):
    summary: str
    steps: list[ToolStep]

    @field_validator("steps")
    @classmethod
    def require_steps(cls, value: list[ToolStep]) -> list[ToolStep]:
        if not value:
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
