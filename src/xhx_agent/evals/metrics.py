from __future__ import annotations

from pydantic import BaseModel


class RunMetrics(BaseModel):
    duration_seconds: float = 0.0
    turns: int = 0
    tokens_estimate: int = 0
    files_changed_count: int = 0
    commands_run_count: int = 0
    repair_attempts: int = 0
    success: bool = False
