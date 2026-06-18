"""运行结果数据模型 —— 纯数据结构，不依赖 RuntimeApp。

从 runtime/app.py 中抽出的 RunResult / PlanPreview，供新旧两栈共用。
A3 删除 app.py 后，这些类型仍会保留。
"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.safety.repair import RepairDecision
from xhx_agent.tools.terminal import TerminalResult


class RunResult(BaseModel):
    """单次运行的完整结果。"""

    run_id: str
    status: str
    turns: int = 0
    changed_files: list[str]
    commands: list[str]
    verification: str
    verification_results: list[TerminalResult] = []
    checkpoint_path: str | None = None
    restore_plan_path: str | None = None
    repair: RepairDecision | None = None
    repair_attempts: int = 0
    summary_path: str
    risk_summary: list[str]
    metrics: RunMetrics | None = None
    mode: str = ""
    answer: str | None = None
    transcript_path: str | None = None


class PlanPreview(BaseModel):
    """计划预览结果。"""

    run_id: str
    status: str
    summary: str
    step_count: int
    context_budget_tokens: int
    context_used_tokens_estimate: int
    trace_path: str
    risk_summary: list[str]
