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


def decide_tool(tool_name: str) -> PolicyDecision:
    if tool_name in {"search", "read_file"}:
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.SAFE,
            reason=f"Tool {tool_name} is read-only.",
        )
    if tool_name == "apply_patch":
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.CONFIRM,
            reason="Structured repository write allowed by apply_patch-only policy.",
        )
    return PolicyDecision(
        decision="deny",
        risk=RiskLevel.DENY,
        reason=f"Tool {tool_name} is not allowed by policy.",
    )
