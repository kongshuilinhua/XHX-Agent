"""五层递进权限检查器。

检查层级（按顺序）：
    Layer 0 — Plan 模式例外放行（只放行 plan 相关工具 + plan 文件写入）
    Layer 1 — 安全命令白名单（is_safe_command 自动放行）
    Layer 1b— 绝对禁令黑名单（DangerousCommandDetector 直接拒绝，任何模式含 bypass 都拦）
    —— bypass/dontAsk 短路（已过绝对禁令层后才放行）——
    Layer 1c— 风险分级闸门（classify_command 判 DENY 直接拒绝，与 decide_terminal 统一）
    Layer 2 — 路径沙箱（PathSandbox 拦截越界文件访问）
    Layer 3 — 规则引擎匹配（YAML/JSON 规则文件显式 allow/deny）
    Layer 4 — 权限模式兜底（mode_decide 按 read/write/command 类别判定）

注意 bypass/dontAsk 短路刻意放在 Layer 1b 之后：bypass 是「用户主动选择全放行」，
但「绝对禁令」（杀 agent 自身、格盘、fork bomb、管道执行远程脚本…）即使 bypass 也不放行。
Layer 1c 起的完整风险分级只在非 bypass 模式生效（bypass 已在其之前短路返回）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector, is_safe_command
from xhx_agent.safety.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from xhx_agent.safety.permissions.rules import RuleEngine, extract_content
from xhx_agent.safety.permissions.sandbox import PathSandbox
from xhx_agent.safety.risk import RiskLevel, classify_command

# Plan 模式下允许自动放行的工具白名单
# present_plan 是主入口（两段式闸门），ExitPlanMode 是兼容别名
_PLAN_MODE_ALLOWED_TOOLS = frozenset(
    {
        "dispatch",
        "Agent",  # 派只读调研子 agent（spawn 出的子 agent 被强制只读，故免审批）
        "AskUserQuestion",
        "ToolSearch",
        "present_plan",
        "ExitPlanMode",
    }
)


@dataclass
class Decision:
    """权限检查结果。"""

    effect: DecisionEffect
    reason: str


class PermissionChecker:
    """五层递进权限检查器。

    使用方式::

        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(workspace),
            rule_engine=RuleEngine(...),
            mode=PermissionMode.DEFAULT,
        )
        decision = checker.check_for(tool_name="read_file", arguments={"path": "/etc/passwd"})
        if decision.effect == "deny":
            raise ...
    """

    def __init__(
        self,
        detector: DangerousCommandDetector,
        sandbox: PathSandbox,
        rule_engine: RuleEngine,
        mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> None:
        self.detector = detector
        self.sandbox = sandbox
        self.rule_engine = rule_engine
        self.mode = mode
        self.plan_file_path: str = ""

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def check(self, tool_name: str, arguments: dict[str, Any], *, tool_category: str = "read") -> Decision:
        """对一次工具调用执行五层递进检查。

        Args:
            tool_name: 工具名
            arguments: 工具参数
            tool_category: 工具类别（``"read"`` / ``"write"`` / ``"command"``）
        """
        content = extract_content(tool_name, arguments)

        # ── Layer 0: Plan 模式例外放行 ──────────────────────────
        if self.mode == PermissionMode.PLAN:
            if tool_name in _PLAN_MODE_ALLOWED_TOOLS:
                return Decision(effect="allow", reason="Plan mode: allowed tool")
            if tool_name in ("WriteFile", "EditFile", "apply_patch") and content:  # noqa: SIM102 保持嵌套以提升可读性
                if self._is_plan_file(content):
                    return Decision(effect="allow", reason="Plan mode: plan file write")

        # ── Layer 1: 安全命令白名单 ──────────────────────────────
        if tool_category == "command" and is_safe_command(content or ""):
            return Decision(effect="allow", reason="Safe read-only command")

        # ── Layer 1b: 绝对禁令黑名单（任何模式含 bypass 都拦）─────
        if tool_category == "command":
            hit, reason = self.detector.detect(content or "")
            if hit:
                return Decision(effect="deny", reason=f"危险命令拦截: {reason}")

        # ── Short-circuit: bypass/dontAsk 模式全放行（YOLO 语义）────
        # 设计取舍：bypass/dontAsk 是「用户主动全放行」，刻意比逐层模式矩阵更强——
        # 允许越界读写工作区外文件、跳过沙箱与规则引擎（对标 Claude Code 的 bypassPermissions）。
        # 但短路的位置至关重要：必须放在「绝对禁令层」（Layer 1b 危险命令黑名单）之后、
        # 沙箱层之前。这样自杀（按映像名杀 python）/ 格盘 / fork bomb / 管道执行远程脚本
        # 这类灾难命令即使 YOLO 也拦得住，而其余一切放行。
        # 维护警告：① 不要把这段挪回 check() 顶部——那会连绝对禁令一起绕过（历史 bug，
        # 曾导致 `taskkill /f /im python.exe` 自杀 + 终端卡死）；② 不要把它下移到沙箱之后，
        # 否则就退化成「bypass 无法越界访问文件」，与既定 YOLO 语义不符。
        if self.mode in (PermissionMode.BYPASS, PermissionMode.DONT_ASK):
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 全放行")

        # ── Layer 1c: 风险分级闸门（与 decide_terminal 统一）──────
        # bypass 已在上方短路，此处只对非 bypass 模式生效：把 risk.py 判 DENY 的命令
        # （taskkill/kill、shell 元字符拼接、解释器内联执行…）一律拒绝，避免两条闸门一拦一漏。
        if tool_category == "command" and content and classify_command(content) is RiskLevel.DENY:
            return Decision(effect="deny", reason="危险命令拦截: 命令被风险分级判定为 DENY")

        # ── Layer 2: 路径沙箱 ────────────────────────────────────
        if tool_category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(effect="deny", reason=f"路径沙箱拦截: {reason}")

        # ── Layer 3: 规则引擎匹配 ────────────────────────────────
        rule_result = self.rule_engine.evaluate(tool_name, content or "")
        if rule_result == "allow":
            return Decision(effect="allow", reason="权限规则放行")
        if rule_result == "deny":
            return Decision(effect="deny", reason="权限规则拒绝")

        # ── Layer 4: 权限模式兜底判定 ────────────────────────────
        effect = mode_decide(self.mode, tool_category)  # type: ignore[arg-type]
        if effect == "allow":
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 放行")
        if effect == "deny":
            return Decision(effect="deny", reason=f"权限模式 {self.mode.value} 拒绝")

        # ask → 需要人工确认
        return Decision(effect="ask", reason=f"权限模式 {self.mode.value} 要求确认")

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _is_plan_file(self, path: str) -> bool:
        """检查 *path* 是否为当前 plan 文件。

        多策略匹配逻辑：
        1. 精确匹配 plan_file_path
        2. plan_file_path 为空时检查路径中是否包含 plans 目录
        3. basename 匹配
        4. 判定 plan_file_path 是否在目标路径中
        """
        if not path:
            return False
        # 策略 1: 精确匹配
        if self.plan_file_path and path == self.plan_file_path:
            return True
        # 策略 2: plan_file_path 为空时检查 plans 目录
        if not self.plan_file_path and ".xhx/plans/" in path:
            return True
        # 策略 3: basename 匹配
        if self.plan_file_path:
            try:
                from pathlib import Path as _Path

                plan_base = _Path(self.plan_file_path).name
                target_base = _Path(path).name
                if plan_base and plan_base == target_base:
                    return True
            except Exception:
                pass
        # 策略 4: plan_file_path 是目标路径的前缀
        return bool(self.plan_file_path and path.startswith(self.plan_file_path))
