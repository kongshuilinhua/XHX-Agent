# graph 重铸 P1：LLM planner 出 DAG + 变量替换 + 串行子 agent 执行 Implementation Plan

> **For agentic workers (Gemini):** 这是一份自包含、可冷启动的实施计划。按任务顺序 TDD 推进，步骤用 `- [ ]`。每个任务先写失败测试、再实现、再全量回归。完成后把分支交回，由 Claude 两段式审查 + 真模型联调 + 合并。

**Goal:** 把 `graph` 的 coordinator→execute→review 换成 LLMCompiler 式的 **planner → 串行执行 DAG → 轻量综合**：LLM planner 产出带依赖 + `$id` 变量引用的结构化任务 DAG；每个节点交一个隔离子 agent 执行；前序节点输出经变量替换喂给下游。

**Architecture:** planner（LLM，结构化 JSON DAG，保留"闲聊直答"量级匹配）→ `topological_sort` 定序 → 逐节点：变量替换 → 跑子 agent（`run_subagent` explore / `run_write_subagent` edit）→ 回填 `node.result` → 轻量 synthesize 出最终回答。**P1 串行**（不碰并发），节点同时支持 explore 与 edit（串行下 edit 无竞态）。

**Tech Stack:** Python、LangGraph（沿用 graph.py 现有 StateGraph）、Pydantic（DAGNode/DAGPlan）、`run_subagent`/`run_write_subagent`（现成）、`topological_sort`（现成）、pytest。

**P1 明确不做（留后续片）：** 并行执行 + edit 并发硬化（P2）；joiner / 有界 replan（P3）；真实测试验证（P4）。

---

## P1 验证 Gate（最重要）

P1 的真正目的：**验证真 DeepSeek 能否稳定产出可解析的 DAG**。完成后 Claude 会用真模型跑"并行查 2 个点→汇总改 1 处"的任务联调。**这一步通过，才继续 P2–P4。** 因此 planner 的 prompt + 解析鲁棒性（含兜底）是本片重点，务必把"解析失败兜底成单节点"做扎实。

## File Structure

- `src/xhx_agent/planner/modes.py` — `DAGNode` 增 `agent_type` 与 `prompt` 两字段（向后兼容，保留现有 `tool`/`arguments` 供 `DAGScheduler` 既有测试）。
- `src/xhx_agent/orchestrators/graph.py` — 重写：新增 `PLANNER_PROMPT`/`SYNTHESIZE_PROMPT`、`_plan(ctx, client)`、`_substitute_vars(...)`、`_run_dag_node(...)`、`_synthesize(...)`；`run()` 控制流改 planner→execute→synthesize。删除 `_run_worker`/`_review`/`COORDINATOR_PROMPT`/`WORKER_PROMPT`/`REVIEWER_PROMPT`/`Coordination`（被取代）。
- `tests/test_graph_orchestrator.py` — 迁移到新流程（见 Task 5）。

---

### Task 1: DAGNode 增 agent_type / prompt 字段

**Files:** Modify `src/xhx_agent/planner/modes.py`

- [ ] **Step 1: 写失败测试**（`tests/test_planner.py` 末尾）

```python
def test_dagnode_has_agent_fields() -> None:
    from xhx_agent.planner.modes import DAGNode
    n = DAGNode(node_id="n1", description="d", agent_type="edit", prompt="do x", dependencies=[])
    assert n.agent_type == "edit"
    assert n.prompt == "do x"
    # 默认值（向后兼容现有调用）
    d = DAGNode(node_id="n2", description="d")
    assert d.agent_type == "explore" and d.prompt == ""
```

- [ ] **Step 2: 跑测试确认失败** — `python -m pytest tests/test_planner.py::test_dagnode_has_agent_fields -v`（期望 FAIL：未知字段/缺字段）

- [ ] **Step 3: 实现** —— 给 `DAGNode` 加两个带默认值的字段（`tool` 仍保留）：

```python
class DAGNode(BaseModel):
    node_id: str
    description: str = ""
    agent_type: str = "explore"   # "explore"(只读) | "edit"(写)
    prompt: str = ""              # 给子 agent 的指令，可含 $<node_id> 引用前序输出
    tool: str = ""               # 兼容旧 DAGScheduler 测试；新流程不用
    arguments: dict = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    status: str = "pending"
    result: str | None = None
```
> 注意：`description` 原为必填，这里给默认值 `""`（新流程不强制）。确认 `tests/test_planner.py` 既有 `DAGNode(node_id=..., description=..., tool=...)` 用法仍通过。

- [ ] **Step 4: 跑测试确认通过** + `python -m pytest tests/test_planner.py -q`（既有 scheduler/topological 测试不破）

- [ ] **Step 5: 提交** — `git add -A && git commit -m "feat(graph): extend DAGNode with agent_type/prompt for P1"`

---

### Task 2: planner —— LLM 出结构化 DAG（保留闲聊直答 + 解析兜底）

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

新增常量与函数。**完整给出 prompt 与解析（本片核心，务必照抄语义）：**

```python
import json
import re
from xhx_agent.planner.modes import DAGNode, DAGPlan

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

def _plan(ctx: OrchestratorContext, client) -> tuple[str | None, list[DAGNode]]:
    messages = [
        {"role": "system", "content": PLANNER_PROMPT + "\n\n"
         + render_xhx_md(ctx.scan) + render_recalled_memories(ctx.original_workspace, ctx.task)},
        {"role": "user", "content": ctx.task},
    ]
    result = chat_and_count(ctx, client, messages, [], turn=0)
    return _parse_dag(result.content or "", ctx.task)
```

- [ ] **Step 1: 写失败测试** —— 覆盖：①闲聊→answer；②合法 JSON→nodes（含 deps/`$id`）；③带 ```json 围栏仍解析；④非法 JSON→兜底单 edit 节点；⑤`$ref` 不在 deps→兜底；⑥成环→兜底。用 `MagicMock` 喂 `client.chat.return_value`，直接断言 `_parse_dag`/`_plan` 返回。
- [ ] **Step 2: 跑测试确认失败**
- [ ] **Step 3: 实现上面代码**
- [ ] **Step 4: 跑测试确认通过**
- [ ] **Step 5: 提交** — `git commit -m "feat(graph): LLM planner emits structured DAG with parse+fallback"`

---

### Task 3: 变量替换 + 单节点执行（子 agent）

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`

```python
from xhx_agent.orchestrators.subagent import run_subagent, run_write_subagent

def _substitute_vars(prompt: str, done: dict[str, str]) -> str:
    """把 prompt 里的 $<id> 替换成已完成节点的 result。未知 id 原样保留。"""
    return re.sub(r"\$([A-Za-z0-9_]+)", lambda m: done.get(m.group(1), m.group(0)), prompt)

def _run_dag_node(ctx, node: DAGNode, done: dict[str, str], turn: int) -> tuple[list[str], str]:
    """执行单节点：变量替换 → 跑子 agent → 返回 (changed_files, result_text)。"""
    prompt = _substitute_vars(node.prompt, done)
    if node.agent_type == "edit":
        text, changed = run_write_subagent(ctx, description=node.node_id, prompt=prompt, turn=turn)
        return changed, text
    text = run_subagent(ctx, description=node.node_id, prompt=prompt, agent_type="explore", turn=turn)
    return [], text
```

- [ ] **Step 1: 写失败测试** — ①`_substitute_vars("use $n1 and $n2", {"n1":"A","n2":"B"})=="use A and B"`，未知 `$x` 原样；②`_run_dag_node` 对 explore 调 `run_subagent`、对 edit 调 `run_write_subagent`（monkeypatch 这两个，断言收到的 prompt 已替换、changed_files 透传）。
- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 提交** — `git commit -m "feat(graph): variable substitution + sub-agent node executor"`

---

### Task 4: 重写 run() 控制流（planner → 串行执行 → synthesize）

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`

要点（沿用 LangGraph StateGraph，便于 P3 加 replan 回边）：

```python
SYNTHESIZE_PROMPT = (
    "You are the SOLVER of a multi-agent workflow. Given the user's task and each sub-agent's result, "
    "write a concise final answer for the user. Reply in natural language, no tool calls."
)

class _GraphState(TypedDict):
    answer: str | None
    nodes: list             # list[DAGNode]
    changed_files: list[str]

# 控制流：
#   planner ──(闲聊 answer)──→ END
#       └─(有 nodes)→ execute（topological_sort 后逐节点串行：_run_dag_node，回填 node.result，
#                                done[id]=result，累加 changed_files）→ synthesize（一次 LLM 调用）→ END
```

实现细则：
- `planner` 节点：调 `_plan`；若 answer→`{"answer":answer, "nodes":[]}`；否则 emit `graph_planner` 事件 `f"Planned DAG with {len(nodes)} node(s)."`，返回 `{"nodes": nodes}`。
- `route_after_planner`：`"done" if not nodes else "execute"`。
- `execute` 节点：`ordered = topological_sort(nodes)`；`done={}`；逐节点 emit `graph_node` 事件（含 node_id/agent_type）→ `_run_dag_node` → `node.result=text; done[node.node_id]=text`；累加 changed。返回 `{"nodes": nodes, "changed_files": changed}`。
- `synthesize` 节点：用 `SYNTHESIZE_PROMPT` + 任务 + 各 `node.result` 调一次 `chat_and_count`，结果写 `answer`。`execute→synthesize→END`。
- `run()` 末尾：`answer=final.get("answer")`；`changed_files=sorted(set(...))`；`status="success"`（P1 无 review，正常完成即 success；执行期异常→"failed" + risk）；`verification` 仍按有无 changed_files 给 `passed/skipped_no_changes`（P1 不跑真实测试，沿用现有"无验证"语义即可，写 `"not_executed"` 当有改动）。`RunResult(..., answer=answer, mode=ctx.mode or "graph")`，并 `save_transcript` 补齐（与 loop/plan 对齐，graph 现在缺；把 planner+各节点结论组成的消息序列存下）。
- emit 事件名：planner→`graph_planner`、节点→`graph_node`（或复用现有 `graph_worker`，但建议新名以反映 DAG）。**若新增事件名，需在 `src/xhx_agent/tui/textual_app.py:_timeline_line_for_event` 的 graph 事件分支里加上**（现处理 `graph_coordinator/graph_worker/graph_execute/graph_review`）。

- [ ] **Step 1–5（TDD）**：见 Task 5 的端到端测试驱动本任务；实现后逐个跑通；提交 `git commit -m "feat(graph): rebuild run() as planner -> serial DAG -> synthesize"`

---

### Task 5: 测试迁移（删旧 coordinator/worker/reviewer 测试，加新 DAG 流程测试）

**Files:** Modify `tests/test_graph_orchestrator.py`

- [ ] **删除/改写**（旧契约已不存在）：`test_coordinate_parsing`、`test_graph_worker_commits_changes`、`test_graph_reviewer_retry_then_pass`、`test_graph_reviewer_always_fails`、`test_graph_multiple_subtasks`。reviewer/retry 相关**整体删除**（joiner 在 P3 再加回）。
- [ ] **保留并确认**：`test_graph_answers_conversational_directly`——planner 仍支持 `ANSWER:` 直答（断言不变：success、answer 正确、无节点执行事件）。
- [ ] **新增**（用 FakeClient，`monkeypatch graphmod.build_chat_client`；按 system prompt 含 `"PLANNER"`/`"SOLVER"` 区分调用）：
  1. `test_graph_single_edit_node_changes_code`：planner 返回 1 个 edit 节点（JSON），节点真改 `src/calc.py`，断言 `result.changed_files` 含该文件、`result.status=="success"`、`result.answer` 有值。（参考旧 `test_graph_worker_commits_changes` 的 FakeClient apply_patch 信封写法。）
  2. `test_graph_runs_dependent_nodes_with_variable_substitution`：planner 返回 `n1(explore)`、`n2(edit, deps=[n1], prompt 含 $n1)`；monkeypatch `run_subagent` 返回固定结论、`run_write_subagent` 断言它收到的 prompt 里 `$n1` 已被替换成 n1 的结论。
  3. `test_graph_planner_fallback_on_bad_json`：planner 返回非法 JSON → 兜底单 edit 节点 → 仍 success。

- [ ] **Step 末: 全量回归 + ruff**
  - `python -m pytest -q`（期望全绿）
  - `ruff check src/xhx_agent/orchestrators/graph.py src/xhx_agent/planner/modes.py tests/test_graph_orchestrator.py`（期望干净）
- [ ] **提交** — `git commit -m "test(graph): migrate tests to planner/DAG flow (P1)"`

---

## Self-Review（Gemini 写完自查）

- **Gate 就绪**：planner 解析 + 兜底是否扎实？非法 JSON/围栏/悬空 `$ref`/成环 是否都兜底成单节点而非崩溃？
- **能力不回退**：graph 仍能改代码（edit 节点串行）？`test_graph_single_edit_node_changes_code` 通过即证。
- **类型一致**：`DAGNode.agent_type/prompt`、`_plan` 返回 `(answer, nodes)`、`_run_dag_node` 返回 `(changed_files, text)` 在各处一致。
- **占位符扫描**：无 TODO；planner prompt / 解析 / 替换 三段为完整代码。
- **事件链**：若用新事件名，TUI `_timeline_line_for_event` 已同步。

## 交接说明（给 Claude 验收）

- P1 不含并行/joiner/真实验证（分别 P2/P3/P4）。
- 合并前 Claude 会：两段式审查 + `pytest -q` 全量 + **真 DeepSeek 联调（验证能产 DAG + 变量替换跑通）= P1 Gate**；过了才规划 P2。
- 相关：设计 spec `docs/superpowers/specs/2026-06-14-graph-llmcompiler-rebuild-design.md`、[[roadmap-direction]]。
