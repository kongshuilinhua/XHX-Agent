"""Hook 系统：生命周期事件 + 条件匹配 + 动作执行。

来源：mewcode hooks/，适配 XHX-Agent。
"""

from xhx_agent.hooks.compat import HookManagerCompat, hooks_manager
from xhx_agent.hooks.conditions import Condition, ConditionGroup, parse_condition
from xhx_agent.hooks.engine import HookEngine
from xhx_agent.hooks.events import LifecycleEvent
from xhx_agent.hooks.loader import HookConfigError, load_hooks
from xhx_agent.hooks.models import Action, ActionResult, Hook, HookContext, ToolRejectedError

# 向后兼容别名
HooksManager = HookManagerCompat

__all__ = [
    "Action",
    "ActionResult",
    "Condition",
    "ConditionGroup",
    "Hook",
    "HookConfigError",
    "HookContext",
    "HookEngine",
    "HookManagerCompat",
    "HooksManager",
    "LifecycleEvent",
    "ToolRejectedError",
    "hooks_manager",
    "load_hooks",
    "parse_condition",
]
