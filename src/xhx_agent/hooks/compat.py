"""向后兼容层：提供旧 ``hooks_manager.trigger(stage, **kwargs)`` 接口。

内部同时支持旧式 callback hooks 和新式 HookEngine 事件驱动 hooks。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from xhx_agent.hooks.engine import HookEngine
from xhx_agent.hooks.models import HookContext

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 旧 stage → 新 LifecycleEvent 映射
# ---------------------------------------------------------------------------

_STAGE_TO_EVENT: dict[str, str] = {
    "before_plan": "pre_plan",
    "before_patch": "pre_patch",
    "after_verify": "post_tool_use",
    "before_summary": "compact",
}


class HookManagerCompat:
    """向后兼容的 Hook 管理器。

    同时支持：
    - 旧式 callback: ``register(stage, callback)`` + ``trigger(stage, **kwargs)``
    - 新式事件驱动: 委托给 HookEngine
    """

    def __init__(self, engine: HookEngine | None = None) -> None:
        self._engine = engine or HookEngine()
        self._callbacks: dict[str, list[Callable[..., Any]]] = {}

    @property
    def engine(self) -> HookEngine:
        return self._engine

    # ------------------------------------------------------------------
    # 新式 API
    # ------------------------------------------------------------------

    def trigger(self, stage: str, **kwargs: Any) -> None:
        """触发 hook stage。

        1. 先调用旧式 callback（register 注册的）
        2. 再调用新式 HookEngine（load_hooks 加载的）
        """
        # 旧式 callback
        for cb in self._callbacks.get(stage, []):
            try:
                cb(**kwargs)
            except Exception:
                log.debug("Hook callback %s failed for stage %s", cb.__name__, stage, exc_info=True)

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

    def register(self, stage: str, callback: Callable[..., Any]) -> None:
        """注册旧式 callback hook（向后兼容）。

        Raises:
            ValueError: *stage* 不在已知的有效阶段中。
        """
        valid_stages = {"before_plan", "before_patch", "after_verify", "before_summary"}
        if stage not in valid_stages:
            raise ValueError(f"Invalid lifecycle stage: {stage}")
        if stage not in self._callbacks:
            self._callbacks[stage] = []
        self._callbacks[stage].append(callback)

    def clear(self) -> None:
        """清空所有 hooks 和 callbacks。"""
        self._engine.clear()
        self._callbacks.clear()

    def reset(self) -> None:
        """Alias for clear — 供测试使用。"""
        self.clear()


# ---------------------------------------------------------------------------
# 模块级单例（替代旧的 skills/hooks.py 中的 hooks_manager）
# ---------------------------------------------------------------------------

hooks_manager = HookManagerCompat()
