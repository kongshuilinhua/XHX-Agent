from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ContextItem(BaseModel):
    kind: str
    source: str
    content: str
    priority: int = 50
    reason: str = ""
    tokens_estimate: int = 0


class ContextDebugRecord(BaseModel):
    kind: str
    source: str
    priority: int
    tokens_estimate: int
    selected: bool
    reason: str


class ContextDebugReport(BaseModel):
    budget_tokens: int
    used_tokens_estimate: int
    reserved_tokens_estimate: int
    selected_count: int
    omitted_count: int
    records: list[ContextDebugRecord] = Field(default_factory=list)


class ContextPack(BaseModel):
    task: str
    mode: str = "linear-edit"
    budget_tokens: int
    used_tokens_estimate: int
    project_summary: dict[str, Any] = Field(default_factory=dict)
    constraints: list[str] = Field(default_factory=list)
    items: list[ContextItem] = Field(default_factory=list)
    omitted: list[str] = Field(default_factory=list)
    debug: ContextDebugReport | None = None

    def to_model_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "budget_tokens": self.budget_tokens,
            "used_tokens_estimate": self.used_tokens_estimate,
            "project_summary": self.project_summary,
            "constraints": self.constraints,
            "context_items": [item.model_dump() for item in self.items],
            "omitted": self.omitted,
            "debug": self.debug.model_dump() if self.debug else None,
        }
