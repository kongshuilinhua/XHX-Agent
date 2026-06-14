# graph 重铸 P2：DAGScheduler 并行执行（explore 并行 / edit 串行安全）Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD 推进，步骤用 `- [ ]`。完成交回，Claude 两段式审查 + 全量回归 + 真模型联调 + 合并。

**Goal:** 把 graph 的 execute 从「逐节点串行」换成 **DAGScheduler 拓扑并行**：无依赖的 explore 节点并发跑，edit 节点串行（互不重叠）。复用现有 `DAGScheduler`（已实现拓扑波次 + 只读并发池 + 写节点单跑）。

**Architecture:** graph 的 execute 节点用 `DAGScheduler.execute(plan, callback)` 驱动；callback 做变量替换 + 调子 agent（`_run_dag_node`）+ 收集 changed_files + emit `graph_node`。关键前提：`DAGScheduler` 把 edit 节点判为「写」→ 由它已有的「写节点一次只跑一个、且不与读并发」语义保证 edit 串行安全，**无需新增锁/uuid**。

**Tech Stack:** 现有 `DAGScheduler`/`topological_sort`、`concurrent.futures`（DAGScheduler 内部已用）、graph 的 `_run_dag_node`/`_substitute_vars`（P1 已有）、pytest、`threading` 并发断言。

---

## 范围与边界（重要）

- **本计划 P2 = 并行执行，explore 并发 / edit 串行**。这是安全增量：explore 子 agent 只读、隔离，并发无害（同 loop 的 `1fc731a`）；edit 子 agent 由 DAGScheduler 串行调度（写节点一次一个）→ 不会有 worktree 创建竞态、不会有 `_merge_into_parent` 合并竞态，**因此本片不需要加锁/uuid/worktree 串行化**。
- **不在本计划（→ 另开 P2b）**：**edit 节点之间也并行**。那需要真正的并发硬化（`OrchestratorContext` 加 `subagent_lock`、`subagent.py` 的 `sub_run_id` 改 uuid 防撞、`_merge_into_parent` 加锁、`git worktree add` 创建串行化）+ 专门的竞态测试。git worktree 并发创建有真实锁争用风险，值得单独切片重测，不塞进 P2。
- **不在本计划**：joiner/replan（P3）、真实验证（P4）。
- 这是对 spec 原 P2 的细化：spec 把「edit 并行 + 硬化」也算进 P2，这里拆成 P2(安全并行) + P2b(edit 并行硬化)，降风险、便于 Gemini 落地。

## 当前状态（P1 后）

`graph.py` 的 execute 节点现在是串行 for 循环：
```python
ordered = topological_sort(nodes)
done = {}
for node in ordered:
    emit graph_node ...
    ch, text = _run_dag_node(ctx, node, done, turn=1)
    node.result = text; node.status = "success"; done[node.node_id] = text
    changed.extend(ch)
```
`DAGScheduler.execute(plan, cb)`（`src/xhx_agent/planner/planner.py`）已实现：拓扑波次；每波 `readonly_nodes` 进线程池并发、`write_nodes` 一次只跑一个；回调签名 `cb(node) -> (success: bool, summary: str)`；它负责把 `summary` 写进 `node.result`、维护 `node.status`、失败则下游 `blocked`；整体成功返回 True。**但它用 `node.tool in ("apply_patch","terminal")` 判写**——graph 的新节点 `node.tool` 为空、用 `agent_type`，所以必须先教它认 `agent_type=="edit"`（Task 1）。

## File Structure

- `src/xhx_agent/planner/planner.py` — `DAGScheduler` 的读/写分类加上 `agent_type=="edit"`（Task 1）。
- `src/xhx_agent/orchestrators/graph.py` — execute 节点改用 `DAGScheduler`；`_GraphState` 加 `dag_ok`；run() 据 `dag_ok` 定 status（Task 2）。
- `tests/test_planner.py` — DAGScheduler 把 edit 判为写的测试。
- `tests/test_graph_orchestrator.py` — 并发证明 + edit 串行 + 失败传播测试。

---

### Task 1: DAGScheduler 把 edit 节点判为「写」

**Files:** Modify `src/xhx_agent/planner/planner.py`；Test `tests/test_planner.py`

- [ ] **Step 1: 写失败测试**（`tests/test_planner.py` 末尾）

```python
def test_dag_scheduler_serializes_edit_nodes() -> None:
    import threading
    import time
    from xhx_agent.planner.modes import DAGNode, DAGPlan
    from xhx_agent.planner.planner import DAGScheduler

    def make(agent_type):
        # 两个无依赖同类型节点；用并发计数器测最大并发度
        return DAGPlan(root="demo", nodes=[
            DAGNode(node_id="a", agent_type=agent_type, dependencies=[]),
            DAGNode(node_id="b", agent_type=agent_type, dependencies=[]),
        ])

    def run(plan):
        lock = threading.Lock(); cur = {"n": 0, "max": 0}
        def cb(node):
            with lock:
                cur["n"] += 1; cur["max"] = max(cur["max"], cur["n"])
            time.sleep(0.05)  # 留出重叠窗口
            with lock:
                cur["n"] -= 1
            return True, "ok"
        DAGScheduler(__import__("pathlib").Path("demo")).execute(plan, cb)
        return cur["max"]

    assert run(make("explore")) == 2   # explore 并发
    assert run(make("edit")) == 1      # edit 串行（一次只跑一个）
```

- [ ] **Step 2: 跑确认失败** — `python -m pytest tests/test_planner.py::test_dag_scheduler_serializes_edit_nodes -v`（期望 FAIL：当前 edit 被当只读 → 并发 → max==2）

- [ ] **Step 3: 实现** —— 在 `DAGScheduler.execute` 的读/写分类处（现为 `n.tool in ("apply_patch","terminal")`）改为同时认 `agent_type`：

```python
            def _is_write(n) -> bool:
                return n.tool in ("apply_patch", "terminal") or getattr(n, "agent_type", "") == "edit"

            readonly_nodes = [n for n in ready_nodes if not _is_write(n)]
            write_nodes = [n for n in ready_nodes if _is_write(n)]
```

- [ ] **Step 4: 跑确认通过** + `python -m pytest tests/test_planner.py -q`（既有 DAGScheduler 测试不破——它们用 `tool="read_file"`，仍判只读）

- [ ] **Step 5: 提交** — `git commit -m "feat(graph): DAGScheduler classifies edit nodes as write (serial)"`

---

### Task 2: graph execute 改用 DAGScheduler

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

**实现**：

1. `_GraphState` 加字段 `dag_ok: bool`。

2. 替换 `execute` 节点为（完整代码）：

```python
        def execute(state: _GraphState) -> dict[str, Any]:
            import threading

            from xhx_agent.planner.modes import DAGPlan
            from xhx_agent.planner.planner import DAGScheduler

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

            dag_ok = DAGScheduler(ctx.original_workspace).execute(plan, _cb)
            return {
                "nodes": nodes,
                "changed_files": state["changed_files"] + changed,
                "dag_ok": dag_ok,
            }
```

3. `run()` 里：
   - 初始 invoke 字典加 `"dag_ok": True`（与现有 `"nodes": [], "changed_files": [], "answer": None` 并列）。
   - except 兜底字典也加 `"dag_ok": False`。
   - 取完 `answer/changed_files` 后，加失败判定（放在现有 `if status != "failed": status = "success"` 之前）：
```python
        if status != "failed" and not final.get("dag_ok", True):
            status = "failed"
            risks.append("One or more DAG nodes failed.")
```

> 删掉 P1 execute 里原有的 `from ... import topological_sort` 串行循环 + 手动 status/emit（被 DAGScheduler 接管）。变量替换语义不变（`_substitute_vars` 仍由 `_run_dag_node` 调用）。

- [ ] **Step 1: 写失败测试**（`tests/test_graph_orchestrator.py`）

```python
def test_graph_runs_independent_explore_nodes_in_parallel(tmp_path, monkeypatch):
    """两个无依赖 explore 节点应并发执行（Barrier 证明；串行则超时凑不齐）。"""
    import threading
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    barrier = threading.Barrier(2, timeout=5)
    done_prompts: list[str] = []

    def fake_run_subagent(ctx, description, prompt, agent_type, turn):
        barrier.wait()
        done_prompts.append(prompt)
        return f"explored:{prompt}"

    monkeypatch.setattr(graphmod, "run_subagent", fake_run_subagent)

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(content=None, tool_calls=[ToolCall(
                    id="p1", name="submit_dag", arguments={"nodes": [
                        {"id": "n1", "agent_type": "explore", "prompt": "look A", "deps": []},
                        {"id": "n2", "agent_type": "explore", "prompt": "look B", "deps": []},
                    ]})])
            return ChatResult(content="synthesized")  # SOLVER

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("investigate", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert sorted(done_prompts) == ["look A", "look B"]   # 都越过 barrier == 真并行
    assert result.answer == "synthesized"
```

  另加（同文件）：
  - `test_graph_dependent_nodes_still_substitute`（沿用 P1 的 `test_graph_runs_dependent_nodes_with_variable_substitution` 断言，确认改 DAGScheduler 后 `$n1` 仍被替换成 n1 的 result——若 P1 那个测试已覆盖，确认它仍通过即可，不必新增）。
  - `test_graph_node_failure_marks_failed`：一个 explore 节点抛异常（fake_run_subagent raise）→ `result.status == "failed"`、risk 含 "DAG nodes failed"。

- [ ] **Step 2: 跑确认失败**（并发测试在串行实现下会因 barrier 超时而 done_prompts 为空 → 失败）
- [ ] **Step 3: 实现上面 1/2/3**
- [ ] **Step 4: 跑确认通过**
- [ ] **Step 5: 全量回归 + ruff**
  - `python -m pytest -q`（期望全绿）
  - `ruff check src/xhx_agent/orchestrators/graph.py src/xhx_agent/planner/planner.py tests/test_graph_orchestrator.py tests/test_planner.py`（干净）
- [ ] **Step 6: 提交** — `git commit -m "feat(graph): execute DAG via DAGScheduler (parallel explore, serial edit)"`

---

## Self-Review（Gemini 自查）

- **并行真生效**：`test_graph_runs_independent_explore_nodes_in_parallel` 通过 = 两 explore 真并发。
- **edit 串行安全**：Task 1 测试 max==1 = edit 不重叠 → 无需锁/uuid（这是本片不做硬化的前提，务必确认这条测试是绿的）。
- **变量替换不回退**：依赖节点的 `$id` 仍被替换（P1 的依赖测试仍绿）。
- **失败传播**：节点异常 → dag_ok False → status failed。
- **接口不变**：execute 仍返回 `nodes`/`changed_files`，新增 `dag_ok`；synthesize 仍读 `node.result`（DAGScheduler 已回填）。

## 交接说明（给 Claude 验收）

- 本片不含 edit 并行（→ P2b）、joiner（P3）、真实验证（P4）。
- 合并前 Claude：两段式审查 + `pytest -q` 全量 + 真 DeepSeek 联调（跑一个"并行查 2 模块→综合"的任务，确认 explore 真并发、edit 串行、变量替换正确）。
- 相关：spec `docs/superpowers/specs/2026-06-14-graph-llmcompiler-rebuild-design.md`、P1 计划同目录、[[roadmap-direction]]。
