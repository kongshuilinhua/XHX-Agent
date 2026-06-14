# graph 重铸 P3：joiner + 有界 replan Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD。**这片把"嘴上 synthesize"换成真 joiner（tool-calling 判 finish/replan）+ 有界重规划回路**，并补齐 replan 的**正确性短板（edit 跨轮不丢改）**。完成交回，Claude 两段式审查 + 全量回归 + 真模型联调（含一次 replan）+ 合并。

**Goal:** 给 `graph` 一个真正的复盘回路：DAG 执行完后，joiner（LLM，tool-calling）看各节点结果**判定收尾还是重规划**；重规划有界（≤`max_graph_replans`，默认 2），回 planner 带着"反馈 + 上一轮结果"产出**纠偏 DAG**；预算耗尽时 joiner 只给 `finish` 工具、强制收尾。

**Architecture:** 控制流由 `planner → execute → synthesize → END` 改为带回边的：
```
entry → planner ──(answer_user 直答)──────────────→ END
            │
            └─(submit_dag)→ execute → joiner ──(finish)→ END
                              ▲                   │
                              └────(replan)───────┘   (≤ max_graph_replans 轮)
```
- `synthesize` 节点（"You are the SOLVER…" 纯文本汇总）**替换为** `joiner` 节点：tool-calling 二选一 `finish(text)` / `replan(reason)`。`replan_count < max` 时给两个工具；到上限只给 `finish`（保证必出最终答案，无需额外兜底调用）。
- joiner 选 `replan` → `replan_count+=1`、把 `reason` 存进 `joiner_feedback`、路由回 `planner`。
- `planner` 在 replan 轮读 `joiner_feedback` + 上一轮 `nodes`（含 result），拼出纠偏指令再调 `_plan`。
- **edit 跨轮正确性（本片关键，别漏）**：见下"关键正确性分析"。

**Tech Stack:** LangGraph 条件边/回边、tool-calling（`chat_and_count`）、`DAGScheduler`、git worktree（`WorktreeContext`）、pytest（含真实 git 仓库的跨轮 edit 测试）。

**前置：** P1（tool-calling planner）、P2（并行 explore/串行→P2b 并行 edit）、P2b（edit 并行 + 并发硬化 + worktree 清理修复）均已合并到 main。

---

## 范围与边界

- **本计划 P3 = joiner（finish/replan）+ 有界 replan 回路 + replan 的 edit 跨轮正确性**。
- **行为反转提醒**：现有 `synthesize` 节点 + `SYNTHESIZE_PROMPT`（"You are the SOLVER…"，返回纯文本）被**整体替换**为 `joiner`。**6 个现有测试**里 FakeClient 的 `if "SOLVER" in system: return ChatResult(content="…")` 分支必须迁移成 joiner 契约（返回 `finish` 工具调用），见 Task 2 Step 1 清单。
- **不在本计划**：P4 真实验证（joiner 看真测试结果而非 LLM 意见）——本片 joiner 仍是 LLM 主观判定，这是已知短板，P4 补。
- **不做过度**：joiner 不喂仓库 scan（省 token，靠节点 result 判定即可）；除本片点名的 claims 重置 + worktree 播种外，别动并发锁；别改 explore 路径。

## 关键正确性分析（先读懂再动手，这是本片最容易埋雷处）

replan 第二轮重跑节点时，两类子 agent 行为不同：
- **explore 子 agent**：`run_subagent` 不开 worktree，直接读父工作区（`ctx.tool_context.workspace`）。父工作区已含第一轮 merge 进来的改动 → **第二轮 explore 天然看得到第一轮成果，无需特殊处理。**
- **edit 子 agent**：`run_write_subagent` 用 `git worktree add -b … <dir>` 从 **HEAD** 切出隔离工作树；第一轮的改动只 merge 进了父工作区的**工作区（未提交，不在 HEAD）**。所以**第二轮 edit 子 agent 在自己的 worktree 里看不到第一轮改动**；并且 `ctx.subagent_claims` 仍把第一轮改过的文件标成"已占用"，第二轮想改同一文件会被 `_merge_into_parent` 判 **CONFLICT 丢弃**。

→ 不处理的话 replan 重跑 edit 会**从干净 HEAD 重来、静默丢掉上一轮工作**。两步修复（Task 3）：
1. **每轮重置 claims**：execute 节点入口 `ctx.subagent_claims.clear()`，让纠偏轮可以重写上一轮的文件（同一轮内的并行冲突检测不受影响——清理发生在并行池之前）。
2. **给 edit worktree 播种**：`run_write_subagent` 新增 `seed_files` 参数；建好 worktree 后，把父工作区里这些"前序已改文件"拷进 worktree，使第二轮 edit 子 agent 在隔离树里看得到第一轮成果、在其之上继续改。graph 把"累计已改文件"作为 `seed_files` 传给 edit 节点。

> 注：本片不引入"自动 commit 第一轮改动"这种侵入式方案（会往用户仓库塞提交，违背 worktree 设计的"只 merge 到工作区、交用户审阅"）。播种是非侵入、可回退的。

## File Structure

- `src/xhx_agent/runtime/config.py` — 加 `max_graph_replans: int = 2`。
- `src/xhx_agent/orchestrators/graph.py` — 核心：`JOINER_PROMPT` + `_JOINER_TOOLS`/`_JOINER_FINISH_ONLY` + `_interpret_join`；`joiner` 节点替换 `synthesize`；`_plan` 加 `feedback`/`prior_nodes`；`_GraphState` 加 `replan_count`/`joiner_feedback`/`joiner_decision`；回边 + 路由；execute 入口 `claims.clear()` + 把累计 `changed_files` 作为 `seed_files` 传 edit 节点；run()/transcript/report 文案随之更新。
- `src/xhx_agent/orchestrators/subagent.py` — `run_write_subagent` 加 `seed_files: list[str] | None = None` + `_seed_worktree` 辅助。
- 测试：`tests/test_graph_orchestrator.py`（joiner 契约迁移 + replan 回路 + 预算耗尽 + 跨轮 e2e）、`tests/test_subagent.py`（worktree 播种确定性测试，真实 git 仓库）。

---

### Task 1: config —— max_graph_replans

**Files:** Modify `src/xhx_agent/runtime/config.py`

- [ ] **Step 1: 实现** —— `ProjectConfig` 加字段（放在 `max_parallel_subagents` 附近）：
```python
    max_graph_replans: int = 2  # graph joiner 判定不合格时回 planner 重规划的最多轮数（0=禁用 replan，单轮收尾）；防来回烧 token
```
- [ ] **Step 2: 验证** —— `python -c "from xhx_agent.runtime.config import default_config; assert default_config().max_graph_replans == 2"`；`ruff check src/xhx_agent/runtime/config.py` 干净。
- [ ] **Step 3: 提交** —— `git commit -m "feat(graph): add max_graph_replans config knob"`

---

### Task 2: joiner 节点 + 有界 replan 回路（graph.py 核心）

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

**实现要点（逐处）：**

1. **joiner 提示词 + 工具**（替换 `SYNTHESIZE_PROMPT`）：
```python
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
```

2. **`_interpret_join`**：
```python
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
```

3. **`_plan` 加 replan 上下文**（签名加默认参数，旧调用不破）：
```python
def _plan(ctx, client, feedback: str | None = None, prior_nodes: list[DAGNode] | None = None):
    sys = PLANNER_PROMPT + "\n\n" + render_xhx_md(ctx.scan) + render_recalled_memories(ctx.original_workspace, ctx.task)
    messages = [{"role": "system", "content": sys}, {"role": "user", "content": ctx.task}]
    if feedback:
        prior = "\n".join(f"- {n.node_id} ({n.agent_type}): {n.result}" for n in (prior_nodes or []))
        messages.append({"role": "user", "content":
            "A previous attempt produced these sub-agent results:\n" + (prior or "(none)") +
            "\n\nA reviewer judged the result INSUFFICIENT:\n" + feedback +
            "\n\nProduce a corrective task DAG that specifically addresses the gap (or call answer_user if it "
            "can now be answered directly). Do NOT repeat work already done correctly."})
    result = chat_and_count(ctx, client, messages, _PLANNER_TOOLS, turn=0)
    return _interpret_plan(result, ctx.task)
```

4. **`_GraphState` 加字段**：
```python
class _GraphState(TypedDict):
    answer: str | None
    nodes: list[DAGNode]
    changed_files: list[str]
    dag_ok: bool
    replan_count: int
    joiner_feedback: str | None
    joiner_decision: str | None
```

5. **planner 节点**读反馈：
```python
        def planner(state: _GraphState) -> dict[str, Any]:
            answer, nodes = _plan(ctx, client, feedback=state.get("joiner_feedback"),
                                  prior_nodes=state.get("nodes"))
            if answer is not None:
                emit_event(ctx.event_callback, "graph_planner", "Answered directly (no code work needed).")
                return {"answer": answer, "nodes": []}
            emit_event(ctx.event_callback, "graph_planner", f"Planned DAG with {len(nodes)} node(s).")
            return {"nodes": nodes}
```

6. **execute 节点**：入口 `ctx.subagent_claims.clear()`（每轮重置，见正确性分析）；`seed_files` 接线在 Task 3 接，本任务先只加 `claims.clear()`（其余不变）。

7. **joiner 节点**（替换 synthesize 节点函数体）：
```python
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
```

8. **图装配 + 路由**（替换原 `synthesize` 接线）：
```python
        def route_after_joiner(state: _GraphState) -> str:
            return "replan" if state.get("joiner_decision") == "replan" else "done"

        graph.add_node("joiner", joiner)
        graph.add_edge("execute", "joiner")
        graph.add_conditional_edges("joiner", route_after_joiner, {"replan": "planner", "done": END})
```
（删掉 `add_node("synthesize", …)` 与 `add_edge("execute","synthesize")`/`add_edge("synthesize",END)`。）

9. **invoke 初值**加三字段：`"replan_count": 0, "joiner_feedback": None, "joiner_decision": None`（异常兜底 final 里也补这三个键）。

10. **收尾杂项**：模块 docstring 把 "synthesize(LLM)" 描述改成 joiner；report 的 `plan=[...]` 文案改 `"Graph workflow: planner -> execute -> joiner (bounded replan)."`；transcript 构造里对 `SYNTHESIZE_PROMPT` 的引用改 `JOINER_PROMPT`（保持 transcript 仍能落盘即可，不追求完美还原多轮）。

- [ ] **Step 1: 改/写测试**（`tests/test_graph_orchestrator.py`）

  **(a) 迁移 6 个现有测试的 joiner 契约**——把这些里 FakeClient 的 `if "SOLVER" in system: return ChatResult(content="X")` 改成 `if "JOINER" in system: return ChatResult(content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "X"})])`；断言 `result.answer == "X"` 不变：
  - `test_graph_single_edit_node_changes_code`（"synthesis answer"）
  - `test_graph_runs_dependent_nodes_with_variable_substitution`（"synthesis done"）
  - `test_graph_planner_fallback_on_bad_dag`（"solver finished"）
  - `test_graph_runs_independent_explore_nodes_in_parallel`（fallthrough `content="synthesized"`）
  - `test_graph_runs_independent_edit_nodes_in_parallel`（fallthrough `content="synthesized"`）
  - `test_graph_node_failure_marks_failed`（fallthrough `content="synthesized"`）——它仍应 `status=="failed"`（单节点失败、joiner 直接 finish、final dag_ok False）。
  > 用 system 文本里的 `"JOINER"` 区分 joiner 调用（planner 用 `"PLANNER"`）。注意：这些 FakeClient 若用 `if/return` 链，确保 planner 分支在前、joiner 分支其次、sub-agent 兜底在后。

  **(b) 新增 replan 回路测试**：
  ```python
  def test_graph_joiner_replan_then_finish(tmp_path, monkeypatch):
      """round1 joiner→replan；planner 被带反馈二次调用→新节点；round2 joiner→finish。"""
      import xhx_agent.orchestrators.graph as graphmod
      from xhx_agent.models.types import ChatResult, ToolCall
      from xhx_agent.runtime.app import RuntimeApp
      RuntimeApp(tmp_path).init_project()

      explored = []
      monkeypatch.setattr(graphmod, "run_subagent",
          lambda ctx, description, prompt, agent_type, turn: explored.append(prompt) or "r")

      class FakeClient:
          def __init__(self): self.plans = 0; self.joins = 0
          def chat(self, messages, tools):
              s = messages[0]["content"]
              if "PLANNER" in s:
                  self.plans += 1
                  pid = "a" if self.plans == 1 else "b"
                  return ChatResult(content=None, tool_calls=[ToolCall(id="p", name="submit_dag",
                      arguments={"nodes": [{"id": pid, "agent_type": "explore", "prompt": f"look{self.plans}", "deps": []}]})])
              if "JOINER" in s:
                  self.joins += 1
                  if self.joins == 1:
                      return ChatResult(content=None, tool_calls=[ToolCall(id="j", name="replan",
                          arguments={"reason": "need more"})])
                  return ChatResult(content=None, tool_calls=[ToolCall(id="j", name="finish",
                      arguments={"text": "final answer"})])
              raise AssertionError("unexpected")
      fc = FakeClient()
      monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fc)
      result = RuntimeApp(tmp_path).run_task("t", assume_yes=True, mode="graph")
      assert result.status == "success"
      assert result.answer == "final answer"
      assert fc.plans == 2 and fc.joins == 2        # 重规划了一次
      assert explored == ["look1", "look2"]          # 两轮都执行了
  ```

  **(c) 预算耗尽强制收尾**（joiner 每次都想 replan，断言到 `max_graph_replans` 上限停、且最后一次只拿到 finish 工具 → 必出答案、不死循环）：
  ```python
  def test_graph_replan_budget_exhausted_forces_finish(tmp_path, monkeypatch):
      import xhx_agent.orchestrators.graph as graphmod
      from xhx_agent.models.types import ChatResult, ToolCall
      from xhx_agent.runtime.app import RuntimeApp
      RuntimeApp(tmp_path).init_project()
      monkeypatch.setattr(graphmod, "run_subagent",
          lambda ctx, description, prompt, agent_type, turn: "r")

      class FakeClient:
          def __init__(self): self.plans = 0; self.joins = 0
          def chat(self, messages, tools):
              s = messages[0]["content"]
              if "PLANNER" in s:
                  self.plans += 1
                  return ChatResult(content=None, tool_calls=[ToolCall(id="p", name="submit_dag",
                      arguments={"nodes": [{"id": f"n{self.plans}", "agent_type": "explore", "prompt": "x", "deps": []}]})])
              if "JOINER" in s:
                  self.joins += 1
                  names = [t["function"]["name"] for t in tools]
                  if "replan" in names:               # 还能 replan 就一直 replan
                      return ChatResult(content=None, tool_calls=[ToolCall(id="j", name="replan",
                          arguments={"reason": "again"})])
                  return ChatResult(content=None, tool_calls=[ToolCall(id="j", name="finish",
                      arguments={"text": "forced finish"})])
              raise AssertionError
      fc = FakeClient()
      monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fc)
      result = RuntimeApp(tmp_path).run_task("t", assume_yes=True, mode="graph")
      assert result.answer == "forced finish"
      assert fc.plans == 3      # 默认 max_graph_replans=2 → 1 初规划 + 2 重规划
      assert fc.joins == 3
  ```
  > 关键断言点：最后一次 joiner 的 `tools` 里**没有 replan**（finish-only），证明上限生效、不会无限。

- [ ] **Step 2–4: 失败→实现→通过**（先看红，确认测试真的触达回边逻辑再实现）
- [ ] **Step 5: 提交** —— `git commit -m "feat(graph): joiner with bounded replan loop (replace synthesize)"`

---

### Task 3: replan 的 edit 跨轮正确性（worktree 播种 + claims 重置接线）

**Files:** Modify `src/xhx_agent/orchestrators/subagent.py`、`src/xhx_agent/orchestrators/graph.py`；Test `tests/test_subagent.py`、`tests/test_graph_orchestrator.py`

**实现：**

1. **`run_write_subagent` 加 `seed_files` 参数 + 播种**：
```python
def run_write_subagent(ctx, *, description, prompt, turn=0, seed_files=None):
    ...
    wt = WorktreeContext(ctx.original_workspace, sub_run_id)
    with ctx.subagent_lock:
        wt.__enter__()
    try:
        run_ctx = ctx
        if wt.is_active:
            sub_tool_context = ctx.tool_context.model_copy(update={"workspace": wt.active_path})
            run_ctx = dataclasses.replace(ctx, tool_context=sub_tool_context)
            _seed_worktree(ctx, wt.active_path, seed_files)   # 播种：让本轮 edit 看得到前序已改文件
        answer, changed = _drive_write_loop(run_ctx, prompt, allowed, turn)
        ...
```
辅助函数：
```python
def _seed_worktree(ctx, worktree_root, seed_files) -> None:
    """把父工作区里"前序已改文件"拷进新建 worktree，使后续 edit 在其之上继续改（解决 worktree 从 HEAD 切出看不到未提交改动的问题）。"""
    import shutil
    if not seed_files:
        return
    parent = ctx.tool_context.workspace
    for rel in dict.fromkeys(seed_files):
        if not rel:
            continue
        src = parent / rel
        dest = worktree_root / rel
        if src.exists() and src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
```
> 播种是读父工作区（前序轮已稳定、本轮不再变）+ 写各自隔离 worktree → **可留在锁外并行**（放在 `_drive_write_loop` 之前、`subagent_lock` 之外）。`seed_files=None`（默认）时是 no-op，loop/plan 等其它调用方不受影响。

2. **graph 接线**：`_run_dag_node` 加 `seed_files` 透传给 edit 节点；execute 把"前序轮累计已改文件"作为 `seed_files`：
```python
def _run_dag_node(ctx, node, done, turn, seed_files=None):
    prompt = _substitute_vars(node.prompt, done)
    if node.agent_type == "edit":
        text, changed = run_write_subagent(ctx, description=node.node_id, prompt=prompt, turn=turn,
                                           seed_files=seed_files)
        return changed, text
    text = run_subagent(ctx, description=node.node_id, prompt=prompt, agent_type="explore", turn=turn)
    return [], text
```
execute 里 `_cb` 调用处把 `state["changed_files"]`（即进入本轮前累计的已改文件）作为 `seed_files` 传下去：
```python
            prior_changed = list(state["changed_files"])   # 本轮开始前的累计改动 = 播种源
            def _cb(node: DAGNode) -> tuple[bool, str]:
                done = {n.node_id: n.result for n in nodes if n.result is not None}
                emit_event(...)
                ch, text = _run_dag_node(ctx, node, done, turn=1, seed_files=prior_changed)
                with changed_lock:
                    changed.extend(ch)
                return True, text
```
> Task 2 已在 execute 入口加了 `ctx.subagent_claims.clear()`（每轮重置）；本任务确认它在 `_cb`/线程池启动**之前**执行（顺序：clear → 读 prior_changed → 建池）。两者合起来才让"第二轮重写第一轮文件"既不被 claim 拦、又能在第一轮成果上继续改。

- [ ] **Step 1: 写测试**

  **(a) 播种确定性单测**（`tests/test_subagent.py`，真实 git 仓库）`test_run_write_subagent_seeds_prior_changed_files`：
  - 建真实 git 仓库 + 初始 commit（无 `foo.py`）；构造最小 ctx（真实 `subagent_lock`/`subagent_claims={}`、`original_workspace`/`tool_context.workspace` 指向仓库；参考 test_subagent.py 既有构造）。
  - 在父工作区写入 `foo.py`（内容 `"line1\n"`，模拟"第一轮已改、未提交"），**不**提交。
  - monkeypatch `_drive_write_loop` 为：断言其 `run_ctx.tool_context.workspace`（worktree）下 `foo.py` 存在且内容含 `"line1"`（**证明播种生效**），再返回 `("ok", [])`。
  - 调 `run_write_subagent(ctx, description="d", prompt="p", turn=2, seed_files=["foo.py"])`；断言不抛、worktree 已清理。
  - 反例：`seed_files=None` 时 worktree 下 `foo.py` 不存在（no-op）。

  **(b) e2e 跨轮重写同文件**（`tests/test_graph_orchestrator.py`）`test_graph_replan_reedits_same_file_across_rounds`：
  - 真实 git 仓库（`RuntimeApp(tmp_path).init_project()` 后补 `git init`+commit，或复用既有真 git 套路；若 init_project 不建 git，用 `subprocess` 建最小 git 仓库再 init_project）。
  - FakeClient：planner 第一/二次都产单 edit 节点；joiner 第一次 replan、第二次 finish。**用真实 `run_write_subagent`**（monkeypatch `subagentmod.build_chat_client` 出一个会对同一文件先写半成品、第二轮在其上补全的 edit 子 agent），断言**最终文件 = 第二轮在第一轮基础上的结果**（不是从 HEAD 重来），且 `result.changed_files` 含该文件、`status=="success"`。
  > 此用例验证 claims 重置 + 播种合力。构造较重；若真 git+真子 agent 在 CI 偶发不稳，可退而求其次：用 fake `run_write_subagent` 断言"第二轮拿到的 `seed_files` 含第一轮改的文件"（即接线正确），把"播种真生效"交给 (a) 的单测 + Claude 真模型 gate。**二选一即可，优先 (b)，不稳则降级。**

- [ ] **Step 2: 更新受影响的 fake 签名** —— 凡 monkeypatch 替换 `run_write_subagent` 的测试，其 fake 函数签名都要能接 `seed_files`（加 `seed_files=None`），否则 graph 传 `seed_files=` 会 `TypeError`。至少这几处：`test_graph_runs_dependent_nodes_with_variable_substitution`、`test_graph_planner_fallback_on_bad_dag`、`test_graph_runs_independent_edit_nodes_in_parallel`、`test_variable_substitution_and_node_execution`（直接测 `_run_dag_node` 的 fake，且其对 `_run_dag_node` 的调用若没传 seed_files 靠默认值即可）。**全局搜 `def fake_run_write_subagent` 与 `run_write_subagent(` 逐一核对。**
- [ ] **Step 3–4: 失败→实现→通过**
- [ ] **Step 5: 全量回归 + ruff**
  - `python -m pytest -q`（全绿；用系统 python，不要用被沙箱限制的 .venv/PowerShell 跑终端类测试）
  - `ruff check src/xhx_agent/orchestrators/graph.py src/xhx_agent/orchestrators/subagent.py src/xhx_agent/runtime/config.py tests/test_graph_orchestrator.py tests/test_subagent.py`（干净）
- [ ] **Step 6: 提交** —— `git commit -m "feat(graph): seed edit worktrees + reset claims so replan never loses prior edits"`

---

## Self-Review（Gemini 自查）

- **回路有界**：预算耗尽测试绿；最后一次 joiner 的 tools 不含 replan（finish-only）→ 不可能无限循环。
- **replan 带上下文**：planner 第二次调用确实收到 `joiner_feedback` + 上一轮 result（test_graph_joiner_replan_then_finish 的 `fc.plans==2` + 两轮 explore 都跑）。
- **edit 不丢改**：播种单测 (a) 证明 worktree 里看得到前序文件；e2e (b) 证明跨轮在第一轮成果上续改。`claims.clear()` 在并行池之前。
- **旧契约迁移干净**：6 个 SOLVER→JOINER 测试全绿；`result.answer` 断言不变。
- **签名兼容**：`_plan(feedback=None)`、`run_write_subagent(seed_files=None)` 默认值不破坏 loop/plan 调用方；所有替换 `run_write_subagent` 的 fake 都已接 `seed_files`。
- **无多余改动**：没动并发锁范围、没动 explore 路径、joiner 不喂仓库 scan。

## 交接说明（给 Claude 验收）

- **两段式审查**重点：① 回边是否真有界（finish-only 兜底 vs 额外调用）；② `claims.clear()` 位置在并行池之前；③ 播种留在锁外、`seed_files=None` 时确为 no-op；④ 6 个迁移测试的 joiner 分支契约正确。
- **全量回归**：`python -m pytest -q`（Bash+系统 python；PowerShell/.venv 跑终端类会因沙箱假红，见 [[real-llm-testing]]）。
- **真模型联调 gate（Claude 亲跑，DeepSeek）**：
  1. **正常 finish 路径**：一个多节点任务（并行查 2 处 → 汇总），确认 joiner 一次 finish 收尾、答案合理。
  2. **诱发一次 replan**：给一个第一轮易答不全的任务，观察 joiner→replan→planner 带反馈→第二轮→finish，且**总轮数 ≤ max_graph_replans+1、不死循环**。
  3. **edit 跨轮不丢改**（最关键）：让第一轮做半成品 edit、joiner 判不足 replan、第二轮在**同一文件**补全；确认最终文件是"第一轮+第二轮"的叠加结果（不是从 HEAD 重来），且无 worktree/分支残留（沿用 P2b 的残留检查）。
- 风险最高的是 Task 3 的 edit 跨轮正确性（worktree 从 HEAD 切出的语义）；若真模型联调发现第二轮"看不到"第一轮改动，回看播种是否在 `wt.is_active` 分支内、`seed_files` 是否真把累计 changed 传到位。
- 相关：spec `docs/superpowers/specs/2026-06-14-graph-llmcompiler-rebuild-design.md`、P1/P2/P2b 计划同目录、[[roadmap-direction]]、[[gemini-handoff-lean-plans]]。P4（真实验证：joiner 看真测试结果）留作下一片。
