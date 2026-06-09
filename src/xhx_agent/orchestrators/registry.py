from __future__ import annotations

from xhx_agent.orchestrators.base import Orchestrator
from xhx_agent.orchestrators.dag import DagOrchestrator
from xhx_agent.orchestrators.graph import GraphOrchestrator
from xhx_agent.orchestrators.linear import LinearOrchestrator
from xhx_agent.orchestrators.loop import LoopOrchestrator
from xhx_agent.planner.modes import ExecutionMode

DEFAULT_MODE = "loop"

_ORCHESTRATORS: dict[str, type] = {
    "loop": LoopOrchestrator,  # autonomous unified loop (M2)
    "linear": LinearOrchestrator,  # stop-on-first-change fallback used by auto-classification
    "dag": DagOrchestrator,
    "graph": GraphOrchestrator,  # LangGraph multi-agent workflow (M3)
}


def execution_mode_to_key(mode: ExecutionMode) -> str:
    """Map an auto-classified ExecutionMode to an orchestrator registry key.

    Only DAG_EXECUTE routes to the dag orchestrator; every other mode (direct,
    research-only, linear-edit, ...) runs through the unified linear loop.
    """

    return "dag" if mode == ExecutionMode.DAG_EXECUTE else "linear"


def select_orchestrator(mode: str | None) -> Orchestrator:
    """Pick an orchestrator by explicit mode key (``loop``/``graph``/...).

    Falls back to the default (loop) when ``mode`` is None. Raises ValueError on
    an unknown key so a bad ``--mode`` fails loudly instead of silently.
    """

    key = (mode or DEFAULT_MODE).lower()
    impl = _ORCHESTRATORS.get(key)
    if impl is None:
        available = ", ".join(sorted(_ORCHESTRATORS))
        raise ValueError(f"Unknown orchestrator mode '{mode}'. Available: {available}")
    return impl()
