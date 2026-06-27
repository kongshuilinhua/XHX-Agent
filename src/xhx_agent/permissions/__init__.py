"""权限系统桥接包：从 safety/permissions 重导出。"""

from xhx_agent.safety.permissions.checker import Decision, PermissionChecker
from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector, is_safe_command
from xhx_agent.safety.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from xhx_agent.safety.permissions.rules import (
    Rule,
    RuleEngine,
    build_allow_always_rule,
    extract_content,
    parse_rule,
    split_shell_command,
)
from xhx_agent.safety.permissions.sandbox import PathSandbox

__all__ = [
    "Decision",
    "DecisionEffect",
    "DangerousCommandDetector",
    "PathSandbox",
    "PermissionChecker",
    "PermissionMode",
    "Rule",
    "RuleEngine",
    "build_allow_always_rule",
    "extract_content",
    "is_safe_command",
    "mode_decide",
    "parse_rule",
    "split_shell_command",
]
