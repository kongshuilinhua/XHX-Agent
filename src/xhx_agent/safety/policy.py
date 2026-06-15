"""工具与 terminal 命令的策略判定：把风险档转成「放行 / 确认 / 拒绝」的决定。

terminal 命令的风险分级委托给 safety.risk；工具按白名单判定。两个关键约定：apply_patch 这类结构化写
虽标 CONFIRM，但在 worktree 隔离下自动放行（不逐条弹确认）；mcp_/custom_ 动态工具放行但标 CONFIRM——
它们以 Agent 自身权限运行，没有沙箱隔离。
"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.safety.risk import RiskLevel, classify_command


class PolicyDecision(BaseModel):
    decision: str
    risk: RiskLevel
    reason: str
    requires_user: bool = False


def decide_terminal(command: str, assume_yes: bool = False) -> PolicyDecision:
    """按风险档决定 terminal 命令：deny 直接拒；confirm 默认需确认（assume_yes 可预批）；其余放行。"""
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


def decide_tool(
    tool_name: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    network: bool = False,
) -> PolicyDecision:
    """按工具标志判定：只读→SAFE 放行；破坏性→CONFIRM 放行（worktree 隔离）；
    网络请求工具→CONFIRM 放行（有联网审计）；
    mcp_/custom_ 动态工具→CONFIRM 放行（以 Agent 权限运行、无沙箱）；其余拒绝。"""
    if read_only:
        return PolicyDecision(decision="allow", risk=RiskLevel.SAFE, reason=f"Tool {tool_name} is read-only.")
    if destructive:
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.CONFIRM,
            reason=f"Tool {tool_name} performs writes; allowed under worktree isolation.",
        )
    if network:
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.CONFIRM,
            reason=f"Tool {tool_name} requests network access; allowed under audit policies.",
        )
    if tool_name.startswith("mcp_") or tool_name.startswith("custom_"):
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.CONFIRM,
            reason=(
                f"Dynamic tool {tool_name} allowed; runs with the agent's own privileges "
                "(no isolation sandbox), constrained only by the workspace boundary."
            ),
        )
    return PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=f"Tool {tool_name} is not allowed by policy.")
