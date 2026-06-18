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
    """六种权限模式，与 Claude Code 对齐。"""

    DEFAULT = "default"  # 读自动放行，写/命令需确认
    ACCEPT_EDITS = "acceptEdits"  # 读+写自动放行，命令需确认
    PLAN = "plan"  # Plan 模式：只放行 plan 相关工具
    BYPASS = "bypassPermissions"  # 全部自动放行（危险！）
    CUSTOM = "custom"  # 全部询问用户
    DONT_ASK = "dontAsk"  # 全部自动放行（与 bypass 等价，别名）


# ---------------------------------------------------------------------------
# 模式 → 决策矩阵
# ---------------------------------------------------------------------------

_MODE_MATRIX: dict[PermissionMode, dict[ToolCategory, DecisionEffect]] = {
    PermissionMode.DEFAULT: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.ACCEPT_EDITS: {"read": "allow", "write": "allow", "command": "ask"},
    PermissionMode.PLAN: {"read": "allow", "write": "ask", "command": "ask"},
    PermissionMode.BYPASS: {"read": "allow", "write": "allow", "command": "allow"},
    PermissionMode.CUSTOM: {"read": "ask", "write": "ask", "command": "ask"},
    PermissionMode.DONT_ASK: {"read": "allow", "write": "allow", "command": "allow"},
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
