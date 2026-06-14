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
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.memory.recall import render_recalled_memories
from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client
from xhx_agent.orchestrators._toolturn import chat_and_count
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.orchestrators.subagent import run_subagent, run_write_subagent
from xhx_agent.planner.modes import DAGNode
from xhx_agent.repo_intel.xhx_md import render_xhx_md
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


SYNTHESIZE_PROMPT = (
    "You are the SOLVER of a multi-agent workflow. Given the user's task and each sub-agent's result, "
    "write a concise final answer for the user. Reply in natural language, no tool calls."
)


class _GraphState(TypedDict):
    answer: str | None
    nodes: list[DAGNode]
    changed_files: list[str]


class GraphOrchestrator:
    """graph 范式：LLMCompiler 式串行 DAG 编排工作流。"""

    name = "graph"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.runtime.app import RunResult
        from xhx_agent.runtime.session import save_transcript

        client = build_routed_client(
            ctx.original_workspace,
            role="graph",
            base_profile_name=ctx.profile.name,
            event_callback=ctx.event_callback,
            build_client_func=build_chat_client,
        )

        def planner(state: _GraphState) -> dict[str, Any]:
            answer, nodes = _plan(ctx, client)
            if answer is not None:
                emit_event(ctx.event_callback, "graph_planner", "Answered directly (no code work needed).")
                return {"answer": answer, "nodes": []}
            emit_event(ctx.event_callback, "graph_planner", f"Planned DAG with {len(nodes)} node(s).")
            return {"nodes": nodes}

        def execute(state: _GraphState) -> dict[str, Any]:
            from xhx_agent.planner.planner import topological_sort
            nodes = state["nodes"]
            ordered = topological_sort(nodes)
            done = {}
            changed: list[str] = []
            for node in ordered:
                emit_event(
                    ctx.event_callback,
                    "graph_node",
                    f"Running DAG node {node.node_id} ({node.agent_type}).",
                    node_id=node.node_id,
                    agent_type=node.agent_type,
                )
                node.status = "running"
                try:
                    ch, text = _run_dag_node(ctx, node, done, turn=1)
                    node.result = text
                    node.status = "success"
                    done[node.node_id] = text
                    changed.extend(ch)
                except Exception as e:
                    node.status = "failed"
                    node.result = f"Error: {e}"
                    idx = ordered.index(node)
                    for blocked_node in ordered[idx + 1:]:
                        blocked_node.status = "blocked"
                    raise e
            return {
                "nodes": nodes,
                "changed_files": state["changed_files"] + changed,
            }

        def synthesize(state: _GraphState) -> dict[str, Any]:
            summary = (
                f"Original task: {ctx.task}\n\n"
                "Sub-agent execution results:\n"
                + "\n".join(f"Node {n.node_id} ({n.agent_type}): {n.result}" for n in state["nodes"])
            )
            messages = [
                {"role": "system", "content": SYNTHESIZE_PROMPT},
                {"role": "user", "content": summary},
            ]
            result = chat_and_count(ctx, client, messages, [], turn=0)
            return {"answer": result.content or ""}

        def route_after_planner(state: _GraphState) -> str:
            return "done" if not state.get("nodes") else "execute"

        graph = StateGraph(_GraphState)
        graph.add_node("planner", planner)
        graph.add_node("execute", execute)
        graph.add_node("synthesize", synthesize)
        graph.set_entry_point("planner")
        graph.add_conditional_edges("planner", route_after_planner, {"execute": "execute", "done": END})
        graph.add_edge("execute", "synthesize")
        graph.add_edge("synthesize", END)
        compiled = graph.compile()

        ctx.evidence.write_trace("run_start", {"task": ctx.task, "profile": ctx.profile.name, "orchestrator": "graph"})
        emit_event(ctx.event_callback, "run_start", "Graph run started.", run_id=ctx.run_id, task=ctx.task)

        status = "success"
        risks: list[str] = []
        try:
            final: dict[str, Any] = compiled.invoke({
                "nodes": [], "changed_files": [], "answer": None,
            })
        except Exception as e:
            status = "failed"
            risks.append(f"Execution error: {e}")
            final = {
                "nodes": [],
                "changed_files": [],
                "answer": None,
            }

        answer = final.get("answer")
        changed_files = sorted(set(final.get("changed_files", [])))
        if status != "failed":
            status = "success"

        verification_status = "not_executed" if changed_files else "skipped_no_changes"

        summary = write_report(
            workspace=ctx.original_workspace, run_id=ctx.run_id, task=ctx.task,
            plan=["Graph workflow: planner -> serial execute -> synthesize."],
            changed_files=changed_files, commands=[], verification=verification_status, risks=risks)

        # 保存 transcript 补齐
        messages: list[dict[str, Any]] = []
        messages.append({"role": "system", "content": PLANNER_PROMPT})
        messages.append({"role": "user", "content": ctx.task})
        if answer is not None and not final.get("nodes"):
            messages.append({"role": "assistant", "content": f"ANSWER: {answer}"})
        else:
            nodes_repr = []
            for n in final.get("nodes", []):
                nodes_repr.append({
                    "id": n.node_id,
                    "agent_type": n.agent_type,
                    "prompt": n.prompt,
                    "deps": n.dependencies,
                    "result": n.result,
                    "status": n.status
                })
            messages.append({"role": "assistant", "content": json.dumps({"nodes": nodes_repr}, ensure_ascii=False)})
            for n in final.get("nodes", []):
                messages.append({"role": "user", "content": f"Execute node {n.node_id} ({n.agent_type}): {n.prompt}"})
                messages.append({"role": "assistant", "content": n.result or "No result"})
            if answer is not None:
                messages.append({"role": "system", "content": SYNTHESIZE_PROMPT})
                messages.append({"role": "assistant", "content": answer})

        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)

        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        emit_event(ctx.event_callback, "run_end", "Graph task completed.", run_id=ctx.run_id,
                   status=status, verification=verification_status, changed_files=changed_files,
                   summary_path=str(summary.relative_to(ctx.original_workspace)))

        metrics = RunMetrics(
            duration_seconds=round(time.time() - ctx.start_time, 2), turns=1,
            tokens_estimate=ctx.metrics_tracker.get("tokens", 0), files_changed_count=len(changed_files),
            commands_run_count=0, repair_attempts=0, success=(status == "success"))

        return RunResult(
            run_id=ctx.run_id, status=status, turns=1, changed_files=changed_files,
            commands=[], verification=verification_status,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks, metrics=metrics, mode=ctx.mode or "graph", answer=answer,
            transcript_path=transcript_rel)
