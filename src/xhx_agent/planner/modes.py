from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ExecutionMode(StrEnum):
    DIRECT = "direct"
    RESEARCH_ONLY = "research-only"
    LINEAR_EDIT = "linear-edit"
    PLAN_REVIEW_ACT = "plan-review-act"
    REPAIR_LOOP = "repair-loop"


class DAGNode(BaseModel):
    node_id: str
    description: str
    tool: str
    arguments: dict = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)  # parent node IDs
    status: str = "pending"  # pending, running, success, failed, blocked
    result: str | None = None


class DAGPlan(BaseModel):
    root: str
    nodes: list[DAGNode] = Field(default_factory=list)
    truncated: bool = False


class ReviewDecision(BaseModel):
    passed: bool
    reason: str
    needs_replan: bool = False
