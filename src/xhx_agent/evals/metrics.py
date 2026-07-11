from __future__ import annotations

from pydantic import BaseModel


class RunMetrics(BaseModel):
    duration_seconds: float = 0.0
    turns: int = 0
    tokens_estimate: int = 0
    # 推理侧前缀缓存命中的 prompt token 数与命中率（cache_read / 完整 prompt）。
    # provider 不上报缓存字段（或旧 trace）时保持 0。
    cache_read_tokens: int = 0
    cache_hit_rate: float = 0.0
    files_changed_count: int = 0
    commands_run_count: int = 0
    repair_attempts: int = 0
    success: bool = False
