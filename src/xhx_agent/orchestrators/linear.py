from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class LinearOrchestrator:
    """Single autonomous agent loop: read -> edit -> verify -> repair.

    Default mode. M1 keeps the orchestration body on ``RuntimeApp._run_linear``
    and dispatches to it, making this a thin, symmetric counterpart to
    :class:`~xhx_agent.orchestrators.dag.DagOrchestrator`. M2 deepens the loop.
    """

    name = "linear"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        return ctx.app._run_linear(ctx)
