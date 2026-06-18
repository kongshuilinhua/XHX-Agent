"""工具与 terminal 命令的策略判定：把风险档转成「放行 / 确认 / 拒绝」的决定。

terminal 命令的风险分级委托给 safety.risk；工具按白名单判定。两个关键约定：apply_patch 这类结构化写
虽标 CONFIRM，但在 worktree 隔离下自动放行（不逐条弹确认）；mcp_/custom_ 动态工具放行但标 CONFIRM——
它们以 Agent 自身权限运行，没有沙箱隔离。

新增 ``decide_with_checker`` 路径：通过 PermissionChecker 五层检查
（Plan 白名单 → 安全命令 → 危险命令 → 路径沙箱 → 规则引擎 → 模式兜底）替代简单标志位判定。
"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.safety.permissions.checker import Decision, PermissionChecker
from xhx_agent.safety.risk import RiskLevel, classify_command


class PolicyDecision(BaseModel):
    decision: str
    risk: RiskLevel
    reason: str
    requires_user: bool = False

    @staticmethod
    def from_checker_decision(name: str, d: Decision) -> PolicyDecision:
        """将 PermissionChecker 的 Decision 转为 PolicyDecision。"""
        if d.effect == "deny":
            return PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=d.reason)
        if d.effect == "ask":
            return PolicyDecision(
                decision="confirm",
                risk=RiskLevel.CONFIRM,
                reason=d.reason,
                requires_user=True,
            )
        return PolicyDecision(decision="allow", risk=RiskLevel.SAFE, reason=d.reason)


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
    网络请求工具→CONFIRM 放行（SSRF 护栏兜底）；
    mcp_/custom_ 动态工具→CONFIRM 且 requires_user（需内核按权限模式弹框确认）；其余拒绝。"""
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
                f"Dynamic tool {tool_name} runs with the agent's own privileges (no isolation sandbox); "
                "requires user confirmation before execution."
            ),
            requires_user=True,
        )
    return PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=f"Tool {tool_name} is not allowed by policy.")


def decide_with_checker(
    tool_name: str,
    arguments: dict[str, object],
    checker: PermissionChecker,
    *,
    tool_category: str = "read",
    read_only: bool = False,
    destructive: bool = False,
    network: bool = False,
) -> PolicyDecision:
    """通过 PermissionChecker 五层检查 + 工具标志位回退。

    先走 checker.check()（五层递进），若返回 allow/deny 直接采纳；
    若返回 ask，再结合工具标志位和 permission_mode 决定最终效果。
    """
    # 推断工具类别
    if tool_category not in ("read", "write", "command"):
        if destructive:
            tool_category = "write"
        elif network:
            tool_category = "write"  # 保守：网络工具视为写类别
        elif read_only:
            tool_category = "read"
        else:
            tool_category = "command"

    checker_decision = checker.check(tool_name, dict(arguments), tool_category=tool_category)

    # allow/deny 直接采纳
    if checker_decision.effect in ("allow", "deny"):
        return PolicyDecision.from_checker_decision(tool_name, checker_decision)

    # ask → 需要进一步判断
    # 对标旧版 decide_tool：破坏性/网络工具在工作区隔离下自动放行，
    # 仅 mcp_/custom_ 动态工具需要人工确认（因其无隔离沙箱、以 agent 权限运行）
    if destructive or network:
        return PolicyDecision(
            decision="allow",
            risk=RiskLevel.CONFIRM,
            reason=f"{tool_name}: {checker_decision.reason} — allowed under worktree isolation.",
        )
    if tool_name.startswith("mcp_") or tool_name.startswith("custom_"):
        return PolicyDecision(
            decision="confirm",
            risk=RiskLevel.CONFIRM,
            reason=checker_decision.reason,
            requires_user=True,
        )
    # 其余 ask 转为拒绝（安全默认拒绝，不静默放行）
    return PolicyDecision(
        decision="deny",
        risk=RiskLevel.DENY,
        reason=checker_decision.reason,
    )
