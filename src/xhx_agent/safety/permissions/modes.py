"""六级权限模式矩阵。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# 类型
# ---------------------------------------------------------------------------

DecisionEffect = Literal["allow", "deny", "ask"]
ToolCategory = Literal["read", "write", "command"]


# ---------------------------------------------------------------------------
# 权限模式
# ---------------------------------------------------------------------------


class PermissionMode(StrEnum):
    """权限模式。default / acceptEdits / plan / bypassPermissions 与 Claude Code 外部模式对齐；
    auto 是 XHX 的智能自动模式（规则快判 + 不确定才 LLM）。

    早先的 custom（全部询问）与 dontAsk（等价 bypass 的别名）已删除：custom 从未接入界面，
    dontAsk 在矩阵与短路逻辑上与 bypass 完全等价（非交互全放行也走 bypass）。
    """

    DEFAULT = "default"  # 读自动放行，写/命令需确认
    ACCEPT_EDITS = "acceptEdits"  # 读+写自动放行，命令需确认
    PLAN = "plan"  # Plan 模式：只放行 plan 相关工具
    BYPASS = "bypassPermissions"  # 全部自动放行（绝对禁令与敏感路径仍拦/问）
    AUTO = "auto"  # 智能自动：只读/非破坏性命令自动放行，破坏性→确认，规则拿不准→LLM 判定


# ---------------------------------------------------------------------------
# 模式 → 决策矩阵
# ---------------------------------------------------------------------------

_MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, DecisionEffect]] = {
    PermissionMode.DEFAULT: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.ACCEPT_EDITS: {"read": "allow", "write": "allow", "command": "ask"},
    PermissionMode.PLAN: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.BYPASS: {"read": "allow", "write": "allow", "command": "allow"},
    # auto：读/写自动放行（敏感路径仍由 checker 拦成 ask）；命令的兜底是 ask，但实际命令
    # 在 checker 的 auto 专用分支里按 risk 分级处理（SAFE→放行 / DENY→ask / 不确定→交 LLM），
    # 只有 content 为空等边角才落到这里的 ask。
    PermissionMode.AUTO: {"read": "allow", "write": "allow", "command": "ask"},
}


def mode_decide(mode: PermissionMode, category: ToolCategory) -> DecisionEffect:
    """查询模式矩阵：给定权限模式和工具类别，返回决策效果。"""
    return _MODE_MATRIX[mode][category]


def resolve_permission_mode(raw: str | None) -> PermissionMode:
    """将配置字符串解析为 PermissionMode，无效值回退 DEFAULT。"""
    if raw is None:
        return PermissionMode.DEFAULT
    try:
        return PermissionMode(raw)
    except ValueError:
        return PermissionMode.DEFAULT
