from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import IN_PLACE_WARNING, OrchestratorContext
from xhx_agent.runtime.dag_runner import DAGRunner

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class DagOrchestrator:
    """dag 范式：多节点 DAG 执行，对 DAGRunner 的薄包装。

    把既有 dag-execute 行为收到 Orchestrator 协议之下（Kahn 拓扑排序 + 读写隔离调度）。
    注意：DAG 节点生成目前是启发式基线、不是 LLM 拆解，开放式任务建议走 loop。
    （graph 范式是 M3 另起的 LangGraph 实现，与本 dag 并存，不是替代。）
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
