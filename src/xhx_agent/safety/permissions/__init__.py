"""权限子系统：6 模式矩阵 + 五层递进检查 + YAML/JSON 规则引擎 + 路径沙箱。

与现有的 risk.py（命令风险分级）互补：
- risk.py   → 命令本身是否危险（安全执行边界）
- permissions/ → 工具调用应该放行/拒绝/询问（用户授权边界）
"""

from xhx_agent.safety.permissions.checker import PermissionChecker
from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector, is_safe_command
from xhx_agent.safety.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from xhx_agent.safety.permissions.rules import Rule, RuleEngine, extract_content, parse_rule
from xhx_agent.safety.permissions.sandbox import PathSandbox

__all__ = [
    "DecisionEffect",
    "DangerousCommandDetector",
    "PathSandbox",
    "PermissionChecker",
    "PermissionMode",
    "Rule",
    "RuleEngine",
    "extract_content",
    "is_safe_command",
    "mode_decide",
    "parse_rule",
]
