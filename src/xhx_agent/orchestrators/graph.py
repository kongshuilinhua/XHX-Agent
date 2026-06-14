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
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.memory.recall import render_recalled_memories
from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client
from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, _execute_tool_call_rich, chat_and_count
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.orchestrators.subagent import run_subagent, run_write_subagent
from xhx_agent.planner.modes import DAGNode
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
    "You are the COORDINATOR of a multi-agent coding workflow.\n"
    "Match effort to the request:\n"
    "- If you can fully satisfy it without reading or changing the repository, just answer: reply with a "
    "single message that STARTS with 'ANSWER: ' followed by your full natural-language response.\n"
    "- Otherwise, break it into the FEWEST concrete, independent sub-tasks that can each stand alone "
    "(at most 5), one per line prefixed with '- '. Never split work that is really a single step.\n"
    "No preamble, no numbering, no explanation."
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

PLANNER_PROMPT = (
    "You are the PLANNER of a multi-agent coding workflow.\n"
    "First match effort to the request:\n"
    "- If you can fully satisfy it WITHOUT reading or changing the repository, reply with a single line "
    "starting 'ANSWER: ' followed by your full natural-language response.\n"
    "- Otherwise output a task DAG as ONE JSON object and nothing else:\n"
    '  {"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "...", "deps": []}, ...]}\n'
    "  Rules:\n"
    "  - agent_type is \"explore\" (read-only investigation) or \"edit\" (makes code changes).\n"
    "  - A node prompt may reference a dependency's result with $<id> (e.g. $n1); every $<id> used MUST be "
    "in that node's deps.\n"
    "  - Keep it MINIMAL: split into multiple nodes only when they can truly run independently or one truly "
    "depends on another. A simple task = a single node.\n"
    "  - ids unique; deps must reference existing ids and form a DAG (no cycles).\n"
    "Output ONLY the ANSWER line, or ONLY the JSON object."
)


def _parse_dag(content: str, fallback_task: str) -> tuple[str | None, list[DAGNode]]:
    """解析 planner 输出。返回 (answer, nodes)：answer 非空=闲聊直答；否则 nodes 非空。

    鲁棒性优先：剥围栏、容错；任何解析/校验失败 → 兜底单个 edit 节点（edit 子 agent 可读可写，
    对绝大多数任务都安全）。
    """
    text = (content or "").strip()
    if text.upper().startswith("ANSWER:"):
        return text[len("ANSWER:"):].strip() or text, []
    # 剥可能的 ```json 围栏
    m = re.search(r"\{.*\}", text, re.DOTALL)
    raw = m.group(0) if m else text
    try:
        data = json.loads(raw)
        raw_nodes = data["nodes"]
        nodes = [
            DAGNode(
                node_id=str(n["id"]),
                agent_type="edit" if str(n.get("agent_type", "explore")) == "edit" else "explore",
                prompt=str(n["prompt"]),
                dependencies=[str(d) for d in n.get("deps", [])],
            )
            for n in raw_nodes
        ]
        if not nodes:
            raise ValueError("empty nodes")
        _validate_dag(nodes)  # ids 唯一、deps∈ids、$ref⊆deps、无环（见下）
        return None, nodes
    except Exception:
        return None, [DAGNode(node_id="n1", agent_type="edit", prompt=fallback_task, dependencies=[])]


def _validate_dag(nodes: list[DAGNode]) -> None:
    """校验失败抛异常（由调用方兜底）。"""
    from xhx_agent.planner.planner import topological_sort
    ids = {n.node_id for n in nodes}
    if len(ids) != len(nodes):
        raise ValueError("duplicate ids")
    for n in nodes:
        for d in n.dependencies:
            if d not in ids:
                raise ValueError(f"dangling dep {d}")
        for ref in re.findall(r"\$([A-Za-z0-9_]+)", n.prompt):
            if ref not in n.dependencies:
                raise ValueError(f"var $ {ref} not in deps of {n.node_id}")
    topological_sort(nodes)  # 有环则抛 ValueError


def _plan(ctx: OrchestratorContext, client: Any) -> tuple[str | None, list[DAGNode]]:
    messages = [
        {"role": "system", "content": PLANNER_PROMPT + "\n\n"
         + render_xhx_md(ctx.scan) + render_recalled_memories(ctx.original_workspace, ctx.task)},
        {"role": "user", "content": ctx.task},
    ]
    result = chat_and_count(ctx, client, messages, [], turn=0)
    return _parse_dag(result.content or "", ctx.task)


def _substitute_vars(prompt: str, done: dict[str, str]) -> str:
    """把 prompt 里的 $<id> 替换成已完成节点的 result。未知 id 原样保留。"""
    return re.sub(r"\$([A-Za-z0-9_]+)", lambda m: done.get(m.group(1), m.group(0)), prompt)


def _run_dag_node(ctx: OrchestratorContext, node: DAGNode, done: dict[str, str], turn: int) -> tuple[list[str], str]:
    """执行单节点：变量替换 → 跑子 agent → 返回 (changed_files, result_text)。"""
    prompt = _substitute_vars(node.prompt, done)
    if node.agent_type == "edit":
        text, changed = run_write_subagent(ctx, description=node.node_id, prompt=prompt, turn=turn)
        return changed, text
    text = run_subagent(ctx, description=node.node_id, prompt=prompt, agent_type="explore", turn=turn)
    return [], text


class _GraphState(TypedDict):
    rounds: int
    subtasks: list[str]
    changed_files: list[str]
    worker_results: list[str]
    review_passed: bool
    review_reason: str
    answer: str | None


@dataclass
class Coordination:
    """coordinator 的产出：要么直接回答（answer），要么一组待执行子任务（subtasks）。"""

    answer: str | None
    subtasks: list[str]


def _coordinate(ctx: OrchestratorContext, client: Any) -> Coordination:
    """LLM 判别量级：闲聊/可直接回答→answer 直答；需改代码→拆子任务（解析 '- ' 行，兜底整体单子任务）。"""
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
    content = (result.content or "").strip()
    if content.upper().startswith("ANSWER:"):
        return Coordination(answer=content[len("ANSWER:"):].strip() or content, subtasks=[])
    lines = [ln.strip() for ln in content.splitlines()]
    subtasks = [ln[2:].strip() for ln in lines if ln.startswith("- ") and ln[2:].strip()]
    if not subtasks:
        subtasks = [ctx.task]
    return Coordination(answer=None, subtasks=subtasks[:MAX_SUBTASKS])


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
            coord = _coordinate(ctx, client)
            if coord.answer is not None:
                # 量级匹配：闲聊/可直接回答的问题不拆任务、不启动写 worker，直接给出回答。
                emit_event(ctx.event_callback, "graph_coordinator",
                           "Answered directly (no code work needed).", round=state["rounds"])
                return {"subtasks": [], "answer": coord.answer, "review_passed": True}
            emit_event(ctx.event_callback, "graph_coordinator",
                       f"Decomposed task into {len(coord.subtasks)} sub-task(s).", round=state["rounds"])
            return {"subtasks": coord.subtasks}

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

        def route_after_coordinator(state: _GraphState) -> str:
            # 没有子任务（coordinator 已直接回答）→ 直接结束；否则进入执行。
            return "done" if not state["subtasks"] else "execute"

        def route(state: _GraphState) -> str:
            if state["review_passed"] or state["rounds"] >= max_rounds:
                return "done"
            return "execute"

        graph = StateGraph(_GraphState)
        graph.add_node("coordinator", coordinator)
        graph.add_node("execute", execute)
        graph.add_node("review", review)
        graph.set_entry_point("coordinator")
        graph.add_conditional_edges("coordinator", route_after_coordinator, {"execute": "execute", "done": END})
        graph.add_edge("execute", "review")
        graph.add_conditional_edges("review", route, {"execute": "execute", "done": END})
        compiled = graph.compile()

        ctx.evidence.write_trace("run_start", {"task": ctx.task, "profile": ctx.profile.name, "orchestrator": "graph"})
        emit_event(ctx.event_callback, "run_start", "Graph run started.", run_id=ctx.run_id, task=ctx.task)
        final: dict[str, Any] = compiled.invoke({
            "rounds": 0, "subtasks": [], "changed_files": [], "worker_results": [],
            "review_passed": False, "review_reason": "", "answer": None,
        })

        # 量级匹配：闲聊由 coordinator 直答；编码任务则把 worker 结果作为回答输出（让用户看到做了什么）。
        answer = final.get("answer")
        if answer is None and final["worker_results"]:
            answer = "\n".join(r for r in final["worker_results"] if r)
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
            risk_summary=risks, metrics=metrics, mode=ctx.mode or "graph", answer=answer)
