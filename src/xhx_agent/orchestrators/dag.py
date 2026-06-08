from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import IN_PLACE_WARNING, OrchestratorContext
from xhx_agent.runtime.dag_runner import DAGRunner

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class DagOrchestrator:
    """Multi-node DAG execution. Thin wrapper over the existing DAGRunner.

    M3 will replace this with a LangGraph-based GraphOrchestrator; until then it
    preserves the current dag-execute behaviour behind the Orchestrator protocol.
    """

    name = "dag"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        result = DAGRunner(ctx.app).run_dag(
            task=ctx.task,
            run_id=ctx.run_id,
            evidence=ctx.evidence,
            kernel=ctx.kernel,
            tool_context=ctx.tool_context,
            assume_yes=ctx.assume_yes,
            confirm_callback=ctx.confirm_callback,
            event_callback=ctx.event_callback,
            cancel_check=ctx.cancel_check,
            start_time=ctx.start_time,
            metrics_tracker=ctx.metrics_tracker,
        )
        result.mode = ctx.mode
        if result.status != "success" and not ctx.isolated and result.changed_files:
            result.risk_summary.append(IN_PLACE_WARNING)
        return result
