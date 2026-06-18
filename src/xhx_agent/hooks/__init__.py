"""Hook 系统：生命周期事件 + 条件匹配 + 动作执行。"""

from __future__ import annotations

import logging
from typing import Any

from xhx_agent.hooks.conditions import Condition, ConditionGroup, parse_condition
from xhx_agent.hooks.engine import HookEngine
from xhx_agent.hooks.events import LifecycleEvent
from xhx_agent.hooks.loader import HookConfigError, load_hooks
from xhx_agent.hooks.models import (
    Action,
    ActionResult,
    Hook,
    HookContext,
    ToolRejectedError,
)

log = logging.getLogger(__name__)


def default_verification_hook(timeout: int = 120) -> Hook:
    """构造内置「改完自动跑定向测试」钩子：在 agent 停止时触发 verification 动作。"""
    return Hook(
        id="builtin-verification",
        event="stop",
        action=Action(type="verification", timeout=timeout),
    )


# ---------------------------------------------------------------------------
# 旧 stage → 新 LifecycleEvent 映射
# ---------------------------------------------------------------------------

_STAGE_TO_EVENT: dict[str, str] = {
    "before_plan": "pre_plan",
    "before_patch": "pre_patch",
    "after_verify": "post_tool_use",
    "before_summary": "compact",
}


class HookManager:
    """统一的 Hook 管理器，提供旧式 trigger() 兼容接口。"""

    def __init__(self, engine: HookEngine | None = None) -> None:
        self._engine = engine or HookEngine()
        self._callbacks: dict[str, list[Any]] = {}

    @property
    def engine(self) -> HookEngine:
        return self._engine

    def trigger(self, stage: str, **kwargs: Any) -> None:
        """触发 hook stage：先调用注册的 callback，再委托给 HookEngine。"""
        # 旧式 callback
        for cb in self._callbacks.get(stage, []):
            try:
                cb(**kwargs)
            except Exception:
                log.debug("Hook callback failed for stage %s", stage, exc_info=True)

        # 新式事件驱动
        event = _STAGE_TO_EVENT.get(stage, stage)
        ctx = HookContext(
            event_name=event,
            tool_name=str(kwargs.get("tool", "")),
            file_path=str(kwargs.get("workspace", "")),
        )
        for key in ("patch", "task", "turn", "profile", "results", "run_id"):
            if key in kwargs:
                ctx.tool_args[key] = str(kwargs[key])

        try:
            self._engine.run_hooks_sync(event, ctx)
        except Exception:
            log.debug("HookEngine error for stage %s", stage, exc_info=True)

    def clear(self) -> None:
        """清空所有 hooks。"""
        self._engine.clear()
        self._callbacks.clear()

    def reset(self) -> None:
        """Alias for clear — 供测试使用。"""
        self.clear()

    def register(self, stage: str, callback: Any) -> None:
        """注册旧式 callback hook。

        Raises:
            ValueError: *stage* 不在已知的有效阶段中。
        """
        valid = {"before_plan", "before_patch", "after_verify", "before_summary"}
        if stage not in valid:
            raise ValueError(f"Invalid lifecycle stage: {stage}")
        if stage not in self._callbacks:
            self._callbacks[stage] = []
        self._callbacks[stage].append(callback)


# ---------------------------------------------------------------------------
# 模块级单例
# ---------------------------------------------------------------------------

hooks_manager = HookManager()

# 向后兼容别名
HookManagerCompat = HookManager
HooksManager = HookManager

__all__ = [
    "Action",
    "ActionResult",
    "Condition",
    "ConditionGroup",
    "Hook",
    "HookConfigError",
    "HookContext",
    "HookEngine",
    "HookManager",
    "HookManagerCompat",
    "HooksManager",
    "LifecycleEvent",
    "ToolRejectedError",
    "hooks_manager",
    "load_hooks",
    "parse_condition",
]
