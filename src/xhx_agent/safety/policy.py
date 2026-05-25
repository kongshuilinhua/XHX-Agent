from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.safety.risk import RiskLevel, classify_command


class PolicyDecision(BaseModel):
    decision: str
    risk: RiskLevel
    reason: str
    requires_user: bool = False


def decide_terminal(command: str, assume_yes: bool = False) -> PolicyDecision:
    risk = classify_command(command)
    if risk is RiskLevel.DENY:
        return PolicyDecision(decision="deny", risk=risk, reason="Command is denied by policy.")
    if risk is RiskLevel.CONFIRM and not assume_yes:
        return PolicyDecision(
            decision="confirm",
            risk=risk,
            reason="Command requires user confirmation.",
            requires_user=True,
        )
    return PolicyDecision(decision="allow", risk=risk, reason="Command allowed by policy.")
