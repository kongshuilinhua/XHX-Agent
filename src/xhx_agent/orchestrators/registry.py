"""编排器注册表：按 mode key 选具体编排器。

--mode 显式指定时直接查表；省略时默认 loop。
未知 key 直接报错而非静默兜底，让坏的 --mode 早暴露。
"""

from __future__ import annotations

from xhx_agent.orchestrators.base import Orchestrator
from xhx_agent.orchestrators.loop import LoopOrchestrator
from xhx_agent.orchestrators.plan import PlanOrchestrator
from xhx_agent.orchestrators.team import TeamOrchestrator

DEFAULT_MODE = "loop"

_ORCHESTRATORS: dict[str, type] = {
    "loop": LoopOrchestrator,    # ReAct tool-calling 统一循环
    "plan": PlanOrchestrator,    # Plan-Execute 两阶段
    "team": TeamOrchestrator,    # Coordinator 多 Agent 团队（替代 graph）
}


def select_orchestrator(mode: str | None) -> Orchestrator:
    """按显式 mode key（loop/plan/team）选编排器。

    mode 为 None 时回退到默认（loop）。未知 key 抛 ValueError，让坏的 --mode 直接失败。
    """
    key = (mode or DEFAULT_MODE).lower()
    impl = _ORCHESTRATORS.get(key)
    if impl is None:
        available = ", ".join(sorted(_ORCHESTRATORS))
        raise ValueError(f"Unknown orchestrator mode '{mode}'. Available: {available}")
    return impl()
