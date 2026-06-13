"""graph 范式：tool-calling 多 agent 工作流（LangGraph 显式状态图）。

控制流 = coordinator → execute → review，带条件重试回路（LangGraph StateGraph）。Phase 4 起三个节点
都由**真 LLM + 原生 tool-calling** 驱动（不再是启发式 DAG）：
- coordinator(LLM)：把任务拆成一组有序子任务。
- execute：每个子任务交一个**写型 worker 小循环**（自己的消息历史、tool-calling 真改代码）。
- review(LLM)：判 PASS/FAIL，FAIL 则回 execute 重试（≤ MAX_REVIEW_ROUNDS）。
与统一的 plan/loop 形成对照：graph 的特征是 coordinator/worker/reviewer **多角色显式分工**，协议同样是 tool-calling。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.memory.recall import render_recalled_memories
from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client
from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, _execute_tool_call_rich, chat_and_count
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

MAX_REVIEW_ROUNDS = 2
MAX_SUBTASKS = 5
WORKER_MAX_TURNS = 4
WORKER_TOOLS = {"search", "read_file", "apply_patch"}

COORDINATOR_PROMPT = (
    "You are the COORDINATOR of a multi-agent coding workflow. Break the user's task into a short ordered "
    "list of concrete, independent sub-tasks (at most 5). Output ONLY the list, one sub-task per line prefixed "
    "with '- '. No preamble, no numbering, no explanation. If the task is already atomic, output a single line."
)
WORKER_PROMPT = (
    "You are a WORKER agent in a multi-agent workflow. Accomplish ONLY the assigned sub-task using the tools. "
    "Use relative paths; make all edits with apply_patch (unified diff or *** Begin Patch envelope). "
    "When the sub-task is done, reply with a one-line result and no tool calls."
)
REVIEWER_PROMPT = (
    "You are the REVIEWER of a multi-agent workflow. Given the original task and what the workers changed, "
    "decide whether the work is complete and correct. Reply on a single line: 'PASS' if good, or "
    "'FAIL: <short reason>' if more work is needed."
)


class _GraphState(TypedDict):
    rounds: int
    subtasks: list[str]
    changed_files: list[str]
    worker_results: list[str]
    review_passed: bool
    review_reason: str


def _coordinate(ctx: OrchestratorContext, client: Any) -> list[str]:
    """LLM 把任务拆成子任务列表（解析 '- ' 行；解析不出就整体作为单个子任务）。"""
    messages = [
        {
            "role": "system",
            "content": COORDINATOR_PROMPT
            + "\n\n"
            + render_xhx_md(ctx.scan)
            + render_recalled_memories(ctx.original_workspace, ctx.task),
        },
        {"role": "user", "content": ctx.task},
    ]
    result = chat_and_count(ctx, client, messages, [], turn=0)
    lines = [ln.strip() for ln in (result.content or "").splitlines()]
    subtasks = [ln[2:].strip() for ln in lines if ln.startswith("- ") and ln[2:].strip()]
    if not subtasks:
        subtasks = [ctx.task]
    return subtasks[:MAX_SUBTASKS]


def _run_worker(ctx: OrchestratorContext, client: Any, subtask: str, turn: int) -> tuple[list[str], str]:
    """写型 worker 小循环：受限工具 tool-calling，真改代码，返回 (changed_files, 结果文本)。"""
    schemas = [s for s in ctx.kernel.tool_registry.tool_schemas() if s["function"]["name"] in WORKER_TOOLS]
    messages: list[dict] = [
        {
            "role": "system",
            "content": WORKER_PROMPT
            + "\n\n"
            + render_xhx_md(ctx.scan)
            + render_recalled_memories(ctx.original_workspace, ctx.task),
        },
        {"role": "user", "content": subtask},
    ]
    changed: list[str] = []
    text = ""
    for _ in range(WORKER_MAX_TURNS):
        result = chat_and_count(ctx, client, messages, schemas, turn=0)
        if not result.tool_calls:
            text = result.content or ""
            break
        messages.append({
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })
        for tc in result.tool_calls:
            if tc.name not in WORKER_TOOLS:
                content = f"[graph] tool '{tc.name}' not available to worker."
            else:
                _tc, content, ch, _meta = _execute_tool_call_rich(ctx, tc, turn)
                changed.extend(ch)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
    return changed, text or f"worked on: {subtask}"


def _review(ctx: OrchestratorContext, client: Any, changed_files: list[str], worker_results: list[str]) -> tuple[bool, str]:
    """LLM 评审：PASS / FAIL: reason。"""
    summary = (
        f"Original task: {ctx.task}\n"
        f"Changed files: {sorted(set(changed_files)) or 'none'}\n"
        f"Worker results:\n" + "\n".join(f"- {r}" for r in worker_results)
    )
    result = chat_and_count(
        ctx, client, [{"role": "system", "content": REVIEWER_PROMPT}, {"role": "user", "content": summary}], [], turn=0
    )
    verdict = (result.content or "").strip()
    passed = verdict.upper().startswith("PASS")
    reason = "Review passed." if passed else (verdict or "Review did not pass.")
    return passed, reason


class GraphOrchestrator:
    """graph 范式：tool-calling 多 agent 工作流（coordinator → execute → review，LangGraph）。"""

    name = "graph"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.runtime.app import RunResult

        client = build_routed_client(
            ctx.original_workspace,
            role="graph",
            base_profile_name=ctx.profile.name,
            event_callback=ctx.event_callback,
            build_client_func=build_chat_client,
        )
        max_rounds = min(MAX_REVIEW_ROUNDS, load_config(ctx.original_workspace).max_loop_turns)

        def coordinator(state: _GraphState) -> dict[str, Any]:
            subtasks = _coordinate(ctx, client)
            emit_event(ctx.event_callback, "graph_coordinator",
                       f"Decomposed task into {len(subtasks)} sub-task(s).", round=state["rounds"])
            return {"subtasks": subtasks}

        def execute(state: _GraphState) -> dict[str, Any]:
            changed: list[str] = []
            results: list[str] = []
            for i, subtask in enumerate(state["subtasks"]):
                emit_event(ctx.event_callback, "graph_worker", f"Worker on sub-task {i + 1}: {subtask[:60]}",
                           round=state["rounds"], subtask_index=i)
                ch, text = _run_worker(ctx, client, subtask, turn=state["rounds"] + 1)
                changed.extend(ch)
                results.append(text)
            emit_event(ctx.event_callback, "graph_execute", "Executed sub-tasks.", round=state["rounds"])
            return {
                "changed_files": state["changed_files"] + changed,
                "worker_results": state["worker_results"] + results,
            }

        def review(state: _GraphState) -> dict[str, Any]:
            passed, reason = _review(ctx, client, state["changed_files"], state["worker_results"])
            emit_event(ctx.event_callback, "graph_review", reason, passed=passed, round=state["rounds"])
            return {"review_passed": passed, "review_reason": reason, "rounds": state["rounds"] + 1}

        def route(state: _GraphState) -> str:
            if state["review_passed"] or state["rounds"] >= max_rounds:
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
        final: dict[str, Any] = compiled.invoke({
            "rounds": 0, "subtasks": [], "changed_files": [], "worker_results": [],
            "review_passed": False, "review_reason": "",
        })

        changed_files = sorted(set(final["changed_files"]))
        status = "success" if final["review_passed"] else "failed"
        risks: list[str] = [] if final["review_passed"] else [final["review_reason"]]
        verification_status = ("passed" if status == "success" else "failed") if changed_files else "skipped_no_changes"

        summary = write_report(
            workspace=ctx.original_workspace, run_id=ctx.run_id, task=ctx.task,
            plan=[f"Graph workflow: coordinator -> execute -> review ({final['rounds']} round(s))."],
            changed_files=changed_files, commands=[], verification=verification_status, risks=risks)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        emit_event(ctx.event_callback, "run_end", "Graph task completed.", run_id=ctx.run_id,
                   status=status, verification=verification_status, changed_files=changed_files,
                   summary_path=str(summary.relative_to(ctx.original_workspace)))
        metrics = RunMetrics(
            duration_seconds=round(time.time() - ctx.start_time, 2), turns=final["rounds"],
            tokens_estimate=ctx.metrics_tracker["tokens"], files_changed_count=len(changed_files),
            commands_run_count=0, repair_attempts=max(0, final["rounds"] - 1), success=(status == "success"))
        return RunResult(
            run_id=ctx.run_id, status=status, turns=final["rounds"], changed_files=changed_files,
            commands=[], verification=verification_status,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks, metrics=metrics, mode=ctx.mode or "graph")
