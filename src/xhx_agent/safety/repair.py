from __future__ import annotations

from pydantic import BaseModel

MAX_REPAIR_ATTEMPTS = 2


class RepairDecision(BaseModel):
    should_repair: bool
    attempts_used: int
    max_attempts: int = MAX_REPAIR_ATTEMPTS
    reason: str


def decide_repair(verification_status: str, attempts_used: int, auto_repair_enabled: bool = False) -> RepairDecision:
    if verification_status != "failed":
        return RepairDecision(
            should_repair=False,
            attempts_used=attempts_used,
            reason="Repair is only considered after failed verification.",
        )
    if attempts_used >= MAX_REPAIR_ATTEMPTS:
        return RepairDecision(
            should_repair=False,
            attempts_used=attempts_used,
            reason="Repair attempt limit reached.",
        )
    if not auto_repair_enabled:
        return RepairDecision(
            should_repair=False,
            attempts_used=attempts_used,
            reason="Auto repair is not enabled in v0.2 baseline implementation.",
        )
    return RepairDecision(
        should_repair=True,
        attempts_used=attempts_used,
        reason="Repair is allowed by policy.",
    )
