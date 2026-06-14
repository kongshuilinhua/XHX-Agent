"""编排器注册表：按 mode key 选具体编排器，并把 auto-classify 的 ExecutionMode 映射到 key。

--mode 显式指定时直接查表；省略时由意图分类得到 ExecutionMode，再经 execution_mode_to_key 落到
linear 兜底循环。未知 key 直接报错而非静默兜底，让坏的 --mode 早暴露。
"""

from __future__ import annotations

from xhx_agent.orchestrators.base import Orchestrator
from xhx_agent.orchestrators.graph import GraphOrchestrator
from xhx_agent.orchestrators.linear import LinearOrchestrator
from xhx_agent.orchestrators.loop import LoopOrchestrator
from xhx_agent.orchestrators.plan import PlanOrchestrator
from xhx_agent.planner.modes import ExecutionMode

DEFAULT_MODE = "loop"

_ORCHESTRATORS: dict[str, type] = {
    "plan": PlanOrchestrator,  # 自主 plan-execute（原 loop 改名）
    "loop": LoopOrchestrator,  # ReAct tool-calling 统一循环（Claude Code 式）
    "linear": LinearOrchestrator,  # auto-classification 用的首改即停 fallback
    "graph": GraphOrchestrator,  # LangGraph 多 agent 工作流（M3）
}


def execution_mode_to_key(mode: ExecutionMode) -> str:
    """把 auto-classify 出的 ExecutionMode 映射到编排器注册表 key。

    dag 模式已退役；自动分类的所有意图（direct、research-only、linear-edit…）统一落到 linear 兜底循环。
    显式范式（loop/plan/graph）经 --mode 直选。
    """

    _ = mode  # 保留 API 形参；当前所有自动分类意图都走 linear
    return "linear"


def select_orchestrator(mode: str | None) -> Orchestrator:
    """按显式 mode key（loop/graph/…）选编排器。

    mode 为 None 时回退到默认（loop）。未知 key 抛 ValueError，让坏的 --mode 直接失败而非静默兜底。
    """

    key = (mode or DEFAULT_MODE).lower()
    impl = _ORCHESTRATORS.get(key)
    if impl is None:
        available = ", ".join(sorted(_ORCHESTRATORS))
        raise ValueError(f"Unknown orchestrator mode '{mode}'. Available: {available}")
    return impl()
