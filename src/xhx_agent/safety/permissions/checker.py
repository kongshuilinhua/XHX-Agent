"""五层递进权限检查器。

检查层级（按顺序）：
    Layer 0 — Plan 模式例外放行（只放行 plan 相关工具 + plan 文件写入）
    Layer 1 — 安全命令白名单（is_safe_command 自动放行）
    Layer 1b— 绝对禁令黑名单（DangerousCommandDetector 直接拒绝，任何模式含 bypass 都拦）
    —— 敏感路径豁免（.env/.git/shell 配置/.xhx 权限文件，自动放行模式下仍 ask）——
    —— bypass 短路（已过绝对禁令层与敏感路径豁免后才放行）——
    Layer 2 — 路径沙箱（PathSandbox 拦截越界文件访问）
    Layer 3 — 规则引擎匹配（allow/ask/deny；命令按子命令逐段评估，行为优先级 deny>ask>allow）
    Layer 4 — 权限模式兜底（mode_decide 按 read/write/command 类别判定）

注意 bypass 短路刻意放在 Layer 1b 之后：bypass 是「用户主动选择全放行」，
但「绝对禁令」（杀 agent 自身、格盘、fork bomb、管道执行远程脚本…）即使 bypass 也不放行。

**checker 是「交互闸门」，不是「自动执行闸门」**：它只把绝对禁令（Layer 1b）硬 deny，
其余有风险的命令一律交给 Layer 4 走 ask，由用户/上层裁决。绝不在此层把带 `&&`/管道、
`curl`、按 PID 杀进程这类「正常但有风险」的命令一刀切拒掉——否则 `cd X && python app.py`
这种启动 dev server 的日常操作会被误杀。带元字符命令的拒绝属于「自动执行」语义，
是 risk.classify_command / decide_terminal 的职责，别接进交互 checker。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector, is_safe_command
from xhx_agent.safety.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from xhx_agent.safety.permissions.rules import RuleEngine, extract_content, split_shell_command
from xhx_agent.safety.permissions.sandbox import PathSandbox

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
    """权限检查结果。

    needs_classification: auto 模式下规则拿不准的命令，置 True 让上层（agent_runner）在弹框前
    先调一次 LLM 分类器；分类器放行则静默执行，否则才真正弹框确认。
    """

    effect: DecisionEffect
    reason: str
    needs_classification: bool = False


_SHELL_RC_FILES = frozenset({".bashrc", ".zshrc", ".profile", ".bash_profile", ".zprofile", ".bash_login", ".zshenv"})


def _is_sensitive_file(path: str) -> bool:
    """是否为敏感文件：.env / .git 内部 / shell 启动配置 / .xhx 权限规则文件。

    这些文件即使在自动放行模式下也应强制确认——凭据、版本库、自身权限规则被静默改写
    后果严重（对齐 Claude Code 的 bypass-immune safetyCheck）。
    """
    p = path.replace("\\", "/").lower()
    name = p.rsplit("/", 1)[-1]
    if name == ".env" or name.startswith(".env."):
        return True
    if p == ".git" or p.endswith("/.git") or p.startswith(".git/") or "/.git/" in p:
        return True
    if name in _SHELL_RC_FILES:
        return True
    return p.startswith(".xhx/permissions") or "/.xhx/permissions" in p


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

        # ── 敏感路径豁免：.env / .git / shell 配置 / .xhx 权限文件 ──
        # 即使在自动放行模式（acceptEdits / bypass）下也强制确认，对齐 Claude bypass-immune
        # safetyCheck——防止自动化静默改写凭据 / 版本库 / 自身权限规则。放在 bypass 短路之前，
        # 这样 bypass 也拦得住。
        if (
            tool_category == "write"
            and content
            and self.mode in (PermissionMode.ACCEPT_EDITS, PermissionMode.BYPASS, PermissionMode.AUTO)
            and _is_sensitive_file(content)
        ):
            return Decision(effect="ask", reason="敏感文件写入：自动放行模式下仍需确认")

        # ── Short-circuit: bypass 模式全放行（YOLO 语义）────
        # 设计取舍：bypass 是「用户主动全放行」，刻意比逐层模式矩阵更强——允许越界读写工作区外
        # 文件、跳过沙箱与规则引擎（对标 Claude Code 的 bypassPermissions）。但短路的位置至关重要：
        # 必须放在「绝对禁令层」（Layer 1b）与「敏感路径豁免」之后、沙箱层之前。这样自杀（按映像名
        # 杀 python）/ 格盘 / fork bomb / 管道执行远程脚本这类灾难命令即使 YOLO 也拦得住，而其余放行。
        # 维护警告：① 不要把这段挪回 check() 顶部——那会连绝对禁令一起绕过（历史 bug，曾导致
        # `taskkill /f /im python.exe` 自杀 + 终端卡死）；② 不要下移到沙箱之后，否则退化成
        # 「bypass 无法越界访问文件」，与既定 YOLO 语义不符。
        if self.mode == PermissionMode.BYPASS:
            return Decision(effect="allow", reason=f"权限模式 {self.mode.value} 全放行")

        # ── Layer 2: 路径沙箱 ────────────────────────────────────
        if tool_category in ("read", "write") and content:
            ok, reason = self.sandbox.check(content)
            if not ok:
                return Decision(effect="deny", reason=f"路径沙箱拦截: {reason}")

        # ── Layer 3: 规则引擎匹配 ────────────────────────────────
        # 命令类按子命令逐段评估：任一段 deny→deny；任一段 ask→ask；全部段都 allow→allow；
        # 否则（有段未被规则覆盖）落到 Layer 4。对齐 Claude「组合命令每段都要被 allow」。
        if tool_category == "command" and content:
            sub_effects = [self.rule_engine.evaluate(tool_name, sub) for sub in split_shell_command(content)]
            if "deny" in sub_effects:
                return Decision(effect="deny", reason="权限规则拒绝（子命令）")
            if "ask" in sub_effects:
                return Decision(effect="ask", reason="权限规则要求确认（子命令）")
            if sub_effects and all(e == "allow" for e in sub_effects):
                return Decision(effect="allow", reason="权限规则放行（全部子命令）")
        else:
            rule_result = self.rule_engine.evaluate(tool_name, content or "")
            if rule_result == "allow":
                return Decision(effect="allow", reason="权限规则放行")
            if rule_result == "ask":
                return Decision(effect="ask", reason="权限规则要求确认")
            if rule_result == "deny":
                return Decision(effect="deny", reason="权限规则拒绝")

        # ── auto 模式命令分级：规则快判（SAFE 放行 / DENY 确认），拿不准标记交 LLM ──
        # 跑在用户规则（Layer 3）之后：用户显式规则优先；没规则覆盖时才用 risk 分级。
        # 激进尺度——只有破坏性命令（rm/curl/sudo/解释器内联/危险 git…）才确认，其余尽量自动放。
        if self.mode == PermissionMode.AUTO and tool_category == "command" and content:
            from xhx_agent.safety.risk import RiskLevel, classify_command

            levels = [classify_command(sub) for sub in split_shell_command(content)]
            if any(lvl == RiskLevel.DENY for lvl in levels):
                return Decision(effect="ask", reason="auto: 含破坏性命令，需确认")
            if any(lvl == RiskLevel.CONFIRM for lvl in levels):
                return Decision(effect="ask", reason="auto: 命令需智能判定", needs_classification=True)
            return Decision(effect="allow", reason="auto: 规则判定为安全命令")

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
