"""graph 范式：LLMCompiler 式并行多 agent DAG 工作流（LangGraph 显式状态图）。

控制流 = planner → execute → joiner：
- planner(LLM, tool-calling)：二选一调用 answer_user（纯对话直答）或 submit_dag（产出带依赖+$变量的任务 DAG）。
- execute：按拓扑序逐节点（P1 串行）跑隔离子 agent（explore 只读 / edit 写），前序结果经 $<id> 变量替换喂下游。
- joiner(LLM, tool-calling)：判定收尾或重规划。
与 plan/loop 对照：graph 的特征是显式 DAG 编排 + 多 agent 协作，协议同样是 tool-calling。
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

PLANNER_PROMPT = (
    "You are the PLANNER of a multi-agent coding workflow.\n"
    "Respond by calling EXACTLY ONE tool — never answer in plain text:\n"
    "- If the request requires reading or changing the repository in ANY way (including a single-line edit), "
    "call submit_dag with a MINIMAL task DAG. A simple task = a single node; split into multiple nodes only "
    "when they can run independently or one truly depends on another.\n"
    "- Only if the request is pure conversation or a question answerable WITHOUT the repository, call answer_user.\n"
    "DAG rules: ids unique; every dep references an existing id; no cycles; a node's prompt may use $<id> to "
    "insert a dependency's result, and every $<id> used MUST appear in that node's deps."
)

_PLANNER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "answer_user",
            "description": (
                "Answer the user directly. ONLY for pure conversation or a question you can fully answer "
                "WITHOUT reading or changing the repository."
            ),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Full natural-language answer."}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_dag",
            "description": (
                "Submit a task DAG for any request that requires reading or changing the repository — even a "
                "one-line edit is a single 'edit' node. Sub-agents execute the nodes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "nodes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "agent_type": {"type": "string", "enum": ["explore", "edit"]},
                                "prompt": {
                                    "type": "string",
                                    "description": "Self-contained instruction for the sub-agent; may use $<id>.",
                                },
                                "deps": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["id", "agent_type", "prompt"],
                        },
                    }
                },
                "required": ["nodes"],
            },
        },
    },
]


def _nodes_from_args(raw_nodes: Any, fallback_task: str) -> list[DAGNode]:
    """从 submit_dag 的 nodes 参数构造 + 校验节点；任何失败兜底成单个 edit 节点。"""
    try:
        if isinstance(raw_nodes, str):
            raw_nodes = json.loads(raw_nodes)
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
        return nodes
    except Exception:
        return [DAGNode(node_id="n1", agent_type="edit", prompt=fallback_task, dependencies=[])]


def _interpret_plan(result: Any, task: str) -> tuple[str | None, list[DAGNode]]:
    """解读 planner 的 tool-calling 结果。返回 (answer, nodes)：answer 非空=直答；否则 nodes 非空。

    优先看工具调用：answer_user→直答；submit_dag→DAG。模型没调工具时：有纯文本就当直答
    （闲聊兜底），否则兜底单 edit 节点。
    """
    for tc in result.tool_calls or []:
        args = tc.arguments if isinstance(tc.arguments, dict) else {}
        if tc.name == "answer_user":
            return (str(args.get("text") or "").strip() or "(no answer)"), []
        if tc.name == "submit_dag":
            return None, _nodes_from_args(args.get("nodes"), task)
    content = (result.content or "").strip()
    if content:
        return content, []
    return None, [DAGNode(node_id="n1", agent_type="edit", prompt=task, dependencies=[])]


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


def _plan(
    ctx: OrchestratorContext,
    client: Any,
    feedback: str | None = None,
    prior_nodes: list[DAGNode] | None = None,
) -> tuple[str | None, list[DAGNode]]:
    sys = PLANNER_PROMPT + "\n\n" + render_xhx_md(ctx.scan) + render_recalled_memories(ctx.original_workspace, ctx.task)
    messages = [
        {"role": "system", "content": sys},
        {"role": "user", "content": ctx.task},
    ]
    if feedback:
        prior = "\n".join(f"- {n.node_id} ({n.agent_type}): {n.result}" for n in (prior_nodes or []))
        messages.append({"role": "user", "content":
            "A previous attempt produced these sub-agent results:\n" + (prior or "(none)") +
            "\n\nA reviewer judged the result INSUFFICIENT:\n" + feedback +
            "\n\nProduce a corrective task DAG that specifically addresses the gap (or call answer_user if it "
            "can now be answered directly). Do NOT repeat work already done correctly."})
    result = chat_and_count(ctx, client, messages, _PLANNER_TOOLS, turn=0)
    return _interpret_plan(result, ctx.task)


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


JOINER_PROMPT = (
    "You are the JOINER of a multi-agent coding workflow. You are given the user's task and each "
    "sub-agent's result. Decide by calling EXACTLY ONE tool:\n"
    "- call finish(text=...) with a concise final answer for the user when the results sufficiently "
    "accomplish the task;\n"
    "- call replan(reason=...) ONLY if the results are clearly insufficient, wrong, or a node failed, "
    "explaining precisely what is missing so a new plan can fix it.\n"
    "Prefer finishing. Do not call replan for minor stylistic gaps."
)


def _join_tools(can_replan: bool) -> list[dict]:
    finish = {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "Deliver the final natural-language answer to the user. The work is sufficient.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "Full final answer."}},
                "required": ["text"],
            },
        },
    }
    if not can_replan:
        return [finish]
    replan = {
        "type": "function",
        "function": {
            "name": "replan",
            "description": "Send the task back to the planner because results are insufficient or wrong.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string", "description": "What is missing / wrong."}},
                "required": ["reason"],
            },
        },
    }
    return [finish, replan]


def _interpret_join(result: Any) -> tuple[str, str]:
    """解读 joiner tool-calling。返回 (decision, payload)：('finish', answer) | ('replan', reason)。
    没调工具时把纯文本当 finish 答案兜底。"""
    for tc in result.tool_calls or []:
        args = tc.arguments if isinstance(tc.arguments, dict) else {}
        if tc.name == "finish":
            return "finish", (str(args.get("text") or "").strip() or "(no answer)")
        if tc.name == "replan":
            return "replan", (str(args.get("reason") or "").strip() or "results insufficient")
    return "finish", ((result.content or "").strip() or "(no answer)")


class _GraphState(TypedDict):
    answer: str | None
    nodes: list[DAGNode]
    changed_files: list[str]
    dag_ok: bool
    replan_count: int
    joiner_feedback: str | None
    joiner_decision: str | None


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
            answer, nodes = _plan(ctx, client, feedback=state.get("joiner_feedback"),
                                  prior_nodes=state.get("nodes"))
            if answer is not None:
                emit_event(ctx.event_callback, "graph_planner", "Answered directly (no code work needed).")
                return {"answer": answer, "nodes": []}
            emit_event(ctx.event_callback, "graph_planner", f"Planned DAG with {len(nodes)} node(s).")
            return {"nodes": nodes}

        def execute(state: _GraphState) -> dict[str, Any]:
            import threading

            from xhx_agent.planner.modes import DAGPlan
            from xhx_agent.planner.planner import DAGScheduler

            ctx.subagent_claims.clear()  # 每轮重置，防止前序轮 edit 节点占用导致 CONFLICT
            nodes = state["nodes"]
            plan = DAGPlan(root=str(ctx.original_workspace), nodes=nodes)
            changed: list[str] = []
            changed_lock = threading.Lock()

            def _cb(node: DAGNode) -> tuple[bool, str]:
                # 变量替换：读已完成依赖的 result（DAGScheduler 在依赖波次已把 result 写回节点）。
                done = {n.node_id: n.result for n in nodes if n.result is not None}
                emit_event(
                    ctx.event_callback, "graph_node",
                    f"Running DAG node {node.node_id} ({node.agent_type}).",
                    node_id=node.node_id, agent_type=node.agent_type,
                )
                ch, text = _run_dag_node(ctx, node, done, turn=1)
                with changed_lock:
                    changed.extend(ch)
                return True, text  # 异常交给 DAGScheduler 捕获 → 该节点 failed、下游 blocked

            from xhx_agent.runtime.config import load_config
            max_workers = load_config(ctx.original_workspace).max_parallel_subagents
            dag_ok = DAGScheduler(ctx.original_workspace).execute(plan, _cb, max_workers=max_workers)
            return {
                "nodes": nodes,
                "changed_files": state["changed_files"] + changed,
                "dag_ok": dag_ok,
            }

        def joiner(state: _GraphState) -> dict[str, Any]:
            from xhx_agent.runtime.config import load_config
            can_replan = state["replan_count"] < load_config(ctx.original_workspace).max_graph_replans
            summary = (
                f"Original task: {ctx.task}\n\nSub-agent execution results:\n"
                + "\n".join(f"Node {n.node_id} ({n.agent_type}) [{n.status}]: {n.result}" for n in state["nodes"])
            )
            messages = [{"role": "system", "content": JOINER_PROMPT}, {"role": "user", "content": summary}]
            result = chat_and_count(ctx, client, messages, _join_tools(can_replan), turn=0)
            decision, payload = _interpret_join(result)
            if decision == "replan" and can_replan:
                emit_event(ctx.event_callback, "graph_joiner",
                           f"Replan (round {state['replan_count'] + 1}): {payload[:80]}", decision="replan")
                return {"joiner_decision": "replan", "joiner_feedback": payload,
                        "replan_count": state["replan_count"] + 1}
            emit_event(ctx.event_callback, "graph_joiner", "Finished.", decision="finish")
            return {"joiner_decision": "finish", "answer": payload}

        def route_after_planner(state: _GraphState) -> str:
            return "done" if not state.get("nodes") else "execute"

        def route_after_joiner(state: _GraphState) -> str:
            return "replan" if state.get("joiner_decision") == "replan" else "done"

        graph = StateGraph(_GraphState)
        graph.add_node("planner", planner)
        graph.add_node("execute", execute)
        graph.add_node("joiner", joiner)
        graph.set_entry_point("planner")
        graph.add_conditional_edges("planner", route_after_planner, {"execute": "execute", "done": END})
        graph.add_edge("execute", "joiner")
        graph.add_conditional_edges("joiner", route_after_joiner, {"replan": "planner", "done": END})
        compiled = graph.compile()

        ctx.evidence.write_trace("run_start", {"task": ctx.task, "profile": ctx.profile.name, "orchestrator": "graph"})
        emit_event(ctx.event_callback, "run_start", "Graph run started.", run_id=ctx.run_id, task=ctx.task)

        status = "success"
        risks: list[str] = []
        try:
            final: dict[str, Any] = compiled.invoke({
                "nodes": [], "changed_files": [], "answer": None, "dag_ok": True,
                "replan_count": 0, "joiner_feedback": None, "joiner_decision": None,
            })
        except Exception as e:
            status = "failed"
            risks.append(f"Execution error: {e}")
            final = {
                "nodes": [],
                "changed_files": [],
                "answer": None,
                "dag_ok": False,
                "replan_count": 0,
                "joiner_feedback": None,
                "joiner_decision": None,
            }

        answer = final.get("answer")
        changed_files = sorted(set(final.get("changed_files", [])))
        if status != "failed" and not final.get("dag_ok", True):
            status = "failed"
            risks.append("One or more DAG nodes failed.")
        if status != "failed":
            status = "success"

        verification_status = "not_executed" if changed_files else "skipped_no_changes"

        summary = write_report(
            workspace=ctx.original_workspace, run_id=ctx.run_id, task=ctx.task,
            plan=["Graph workflow: planner -> execute -> joiner (bounded replan)."],
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
                messages.append({"role": "system", "content": JOINER_PROMPT})
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
