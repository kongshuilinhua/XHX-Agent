from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.models.types import ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.planner.agents import ReviewerAgent
from xhx_agent.planner.modes import DAGPlan
from xhx_agent.planner.planner import DAGPlanner, DAGScheduler
from xhx_agent.runtime.events import emit_event
from xhx_agent.tools.terminal import TerminalResult

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

MAX_REVIEW_ROUNDS = 2


class _GraphState(TypedDict):
    rounds: int
    nodes: list[Any]
    success: bool
    review_passed: bool
    review_reason: str
    changed_files: list[str]
    commands: list[str]
    verification_results: list[TerminalResult]


class GraphOrchestrator:
    """Multi-agent workflow via a LangGraph StateGraph (the optional, HPD-style paradigm).

    Control flow is an explicit state graph: coordinator -> execute -> review, with a
    conditional re-execute loop. It reuses the shared base (DAG planner, Kahn scheduler,
    SafeExecutionKernel, ReviewerAgent), and is kept intentionally lean — its purpose is to
    contrast with the unified ``loop`` paradigm, not to be production-grade.
    """

    name = "graph"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.runtime.app import RunResult

        planner = DAGPlanner(ctx.workspace)
        reviewer = ReviewerAgent()

        def coordinator(state: _GraphState) -> dict[str, Any]:
            plan = planner.plan_dag(ctx.task)
            emit_event(
                ctx.event_callback,
                "graph_coordinator",
                f"Decomposed task into {len(plan.nodes)} sub-task(s).",
                round=state["rounds"],
            )
            return {"nodes": plan.nodes}

        def execute(state: _GraphState) -> dict[str, Any]:
            changed: list[str] = []
            commands: list[str] = []
            results: list[TerminalResult] = []

            def execute_node(node: Any) -> tuple[bool, str]:
                if node.tool == "terminal":
                    res = ctx.kernel.run_verification(
                        node.arguments.get("command", ""),
                        assume_yes=ctx.assume_yes,
                        confirm_callback=ctx.confirm_callback,
                        event_callback=ctx.event_callback,
                    )
                    results.append(res)
                    commands.append(node.arguments.get("command", ""))
                    return res.status == "success", res.summary or "ok"
                step = ToolStep(tool=node.tool, arguments=node.arguments)
                tool_result, _trace, policy = ctx.kernel.execute_tool(
                    ctx.tool_context, step, state["rounds"] + 1, ctx.event_callback
                )
                if tool_result is None:
                    return False, policy.reason
                changed.extend(tool_result.changed_files)
                return tool_result.status == "success", tool_result.summary or "ok"

            plan = DAGPlan(root=str(ctx.workspace), nodes=state["nodes"])
            ok = DAGScheduler(ctx.workspace).execute(plan, execute_node)
            emit_event(ctx.event_callback, "graph_execute", "Executed sub-task DAG.", success=ok, round=state["rounds"])
            return {
                "success": ok,
                "changed_files": state["changed_files"] + changed,
                "commands": state["commands"] + commands,
                "verification_results": state["verification_results"] + results,
            }

        def review(state: _GraphState) -> dict[str, Any]:
            decision = reviewer.review(ctx.task, state["changed_files"], state["verification_results"])
            emit_event(
                ctx.event_callback,
                "graph_review",
                decision.reason,
                passed=decision.passed,
                round=state["rounds"],
            )
            return {
                "review_passed": decision.passed,
                "review_reason": decision.reason,
                "rounds": state["rounds"] + 1,
            }

        def route(state: _GraphState) -> str:
            if (state["success"] and state["review_passed"]) or state["rounds"] >= MAX_REVIEW_ROUNDS:
                return "done"
            return "execute"

        graph = StateGraph(_GraphState)
        graph.add_node("coordinator", coordinator)
        graph.add_node("execute", execute)
        graph.add_node("review", review)
        graph.set_entry_point("coordinator")
        graph.add_edge("coordinator", "execute")
        graph.add_edge("execute", "review")
        graph.add_conditional_edges("review", route, {"execute": "execute", "done": END})
        compiled = graph.compile()

        ctx.evidence.write_trace("run_start", {"task": ctx.task, "profile": ctx.profile.name, "orchestrator": "graph"})
        emit_event(ctx.event_callback, "run_start", "Graph run started.", run_id=ctx.run_id, task=ctx.task)
        final: dict[str, Any] = compiled.invoke(
            {
                "rounds": 0,
                "nodes": [],
                "success": False,
                "review_passed": False,
                "review_reason": "",
                "changed_files": [],
                "commands": [],
                "verification_results": [],
            }
        )

        changed_files = sorted(set(final["changed_files"]))
        status = "success" if (final["success"] and final["review_passed"]) else "failed"
        risks: list[str] = []
        if not final["review_passed"]:
            risks.append(final["review_reason"])
        verification_status = ("passed" if status == "success" else "failed") if changed_files else "skipped_no_changes"

        summary = write_report(
            workspace=ctx.original_workspace,
            run_id=ctx.run_id,
            task=ctx.task,
            plan=[f"Graph workflow: coordinator -> execute -> review ({final['rounds']} round(s))."],
            changed_files=changed_files,
            commands=final["commands"],
            verification=verification_status,
            risks=risks,
            verification_results=final["verification_results"],
        )
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        emit_event(
            ctx.event_callback,
            "run_end",
            "Graph task completed.",
            run_id=ctx.run_id,
            status=status,
            verification=verification_status,
            changed_files=changed_files,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
        )
        metrics = RunMetrics(
            duration_seconds=round(time.time() - ctx.start_time, 2),
            turns=final["rounds"],
            tokens_estimate=ctx.metrics_tracker["tokens"],
            files_changed_count=len(changed_files),
            commands_run_count=len(final["commands"]),
            repair_attempts=max(0, final["rounds"] - 1),
            success=(status == "success"),
        )
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            turns=final["rounds"],
            changed_files=changed_files,
            commands=final["commands"],
            verification=verification_status,
            verification_results=final["verification_results"],
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks,
            metrics=metrics,
            mode=ctx.mode,
        )
