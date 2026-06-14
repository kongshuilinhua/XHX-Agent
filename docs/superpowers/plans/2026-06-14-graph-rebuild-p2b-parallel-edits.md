# graph 重铸 P2b：并行 edit 节点 + 并发硬化 Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD。**这是并发切片，最易埋雷——并发竞态测试是验收核心，务必做扎实。** 完成交回，Claude 两段式审查 + 全量回归 + 真模型联调（含并行 edit）+ 合并。

**Goal:** 让 DAG 里**无依赖的 edit 节点也并行执行**（P2 里 edit 被 DAGScheduler 串行）。配套并发硬化：edit 子 agent 各跑在独立 git worktree，**git 操作（worktree 创建/清理）与合并回写串行化**（锁），昂贵的 LLM 循环并行。

**Architecture:** ① DAGScheduler 改为「每波所有就绪节点进同一个线程池」（去掉读/写串行区分），并发度由 `max_workers` 参数封顶；安全不再靠调度器串行写节点，而是下沉到 ② subagent.py 的硬化：`OrchestratorContext` 加 `subagent_lock`；`sub_run_id` 改 uuid 防撞；`run_write_subagent` 把「worktree 创建 / `_merge_into_parent` / worktree 清理」三段 git/共享态操作各自包进锁，LLM 写循环留在锁外并行。

**Tech Stack:** `concurrent.futures`、`threading.Lock`、`uuid`、git worktree（现有 `WorktreeContext`）、`DAGScheduler`、pytest（含真实 git 仓库的并发竞态测试）。

**前置：** P1（tool-calling planner）、P2（DAGScheduler 并行 explore / 串行 edit）、sub-agent 轮数配置化均已合并。

---

## 范围与边界

- **本计划 P2b = edit 节点并行 + 并发硬化**。完成后 explore 与 edit 都并行，安全由 subagent 层的锁/worktree/uuid 保证。
- **行为反转提醒**：P2 加的 `test_dag_scheduler_serializes_edit_nodes`（断言 edit 串行 max==1）在本片要**改成断言并行 max==2**（edit 不再串行）。
- **不在本计划**：joiner/replan（P3）、真实验证（P4）。
- **不做过度**：除本计划点名的三处锁 + uuid + 调度器并行池外，别加别的锁；别改 explore 路径（已并行安全）。

## 关键并发分析（先读懂再动手）

edit 子 agent 的并发危险点**只有三处共享态/外部锁**，其余（LLM 循环、worktree 内的文件读写）天然隔离：
1. **`git worktree add`（创建）**：在主仓库跑，并发创建会争 git 内部锁（refs/worktrees 元数据）→ **串行化**。
2. **`_merge_into_parent`（合并回写）**：读写共享 `ctx.subagent_claims` dict（check-then-set 非原子）+ 往父工作区拷文件 → **串行化**。
3. **`git worktree remove` / `branch -D`（清理）**：又是主仓库 git 锁 → **串行化**。
4. **`sub_run_id` 现用 `len(ctx.subagent_claims)` 取唯一**：并发下两个子 agent 读到同样的 len → 同名 worktree 路径/分支冲突 → **改 uuid**。

LLM 写循环（`_drive_write_loop`）跑在各自 worktree、不碰父态 → **留在锁外并行**（这才是并行的收益所在）。

## File Structure

- `src/xhx_agent/orchestrators/base.py` — `OrchestratorContext` 加 `subagent_lock: threading.Lock`。
- `src/xhx_agent/orchestrators/subagent.py` — `sub_run_id` 改 uuid；`run_write_subagent` 三段 git/合并操作加锁、循环留锁外。
- `src/xhx_agent/planner/planner.py` — `DAGScheduler.execute` 每波所有就绪节点进同一线程池（去读/写区分），加 `max_workers` 参数。
- `src/xhx_agent/runtime/config.py` — 加 `max_parallel_subagents: int = 4`（封顶并发子 agent 数，防嵌套 LLM 调用烧 token/撞限流）。
- `src/xhx_agent/orchestrators/graph.py` — execute 调度时把 `max_parallel_subagents` 作为 `max_workers` 传给 DAGScheduler。
- 测试：`tests/test_planner.py`（调度器并行）、`tests/test_subagent.py`（**并行 edit 竞态：真实 git 仓库**）、`tests/test_graph_orchestrator.py`（e2e 并行 edit）。

---

### Task 1: OrchestratorContext 加 subagent_lock

**Files:** Modify `src/xhx_agent/orchestrators/base.py`

- [ ] **Step 1: 实现** —— 顶部 `import threading`；给 `OrchestratorContext` dataclass 末尾加字段：
```python
    # 并行写子 agent 用：串行化 git worktree 创建/清理 + _merge_into_parent（claims 与拷贝）的临界区。
    subagent_lock: threading.Lock = field(default_factory=threading.Lock)
```
（`field` 已从 dataclasses 导入；确认 import 行有 `from dataclasses import dataclass, field`。）

- [ ] **Step 2: 验证** —— `python -c "from xhx_agent.orchestrators.base import OrchestratorContext"` 不报错；`ruff check src/xhx_agent/orchestrators/base.py` 干净。（无需单测，下游测试覆盖。）
- [ ] **Step 3: 提交** —— `git commit -m "feat(graph): add subagent_lock to OrchestratorContext for parallel writes"`

---

### Task 2: subagent.py —— uuid 防撞 + 三段 git/合并加锁

**Files:** Modify `src/xhx_agent/orchestrators/subagent.py`；Test `tests/test_subagent.py`

**实现：**
1. 顶部 `import uuid`。
2. `run_write_subagent` 里 `sub_run_id` 改：
```python
    sub_run_id = f"{ctx.run_id}-edit{turn}-{uuid.uuid4().hex[:8]}"
```
3. `run_write_subagent` 把 `with WorktreeContext(...) as wt:` 改成手动 enter/exit + 三段锁（**完整替换**那段）：
```python
    wt = WorktreeContext(ctx.original_workspace, sub_run_id)
    with ctx.subagent_lock:          # ① 串行化 worktree 创建（git 锁争用）
        wt.__enter__()
    try:
        run_ctx = ctx
        if wt.is_active:
            sub_tool_context = ctx.tool_context.model_copy(update={"workspace": wt.active_path})
            run_ctx = dataclasses.replace(ctx, tool_context=sub_tool_context)
        answer, changed = _drive_write_loop(run_ctx, prompt, allowed, turn)   # 锁外并行（各自 worktree）
        merge_root = wt.active_path if wt.is_active else ctx.tool_context.workspace
        with ctx.subagent_lock:      # ② 串行化合并（claims + 文件拷贝）
            applied, conflicts = _merge_into_parent(ctx, merge_root, changed, label)
    finally:
        with ctx.subagent_lock:      # ③ 串行化 worktree 清理（git 锁）
            wt.__exit__(None, None, None)
```
> 注意：`dataclasses.replace`/`WorktreeContext` 的 import 已有（原 `with` 版就在用）。逻辑等价于原版，只是把 git/合并三段串行化、循环留并行。

- [ ] **Step 1: 写测试**（`tests/test_subagent.py`）

  分工：**真实 git worktree 下两个 edit 子 agent 同时跑的端到端并行竞态，由 Claude 在真模型联调 gate 验证**（多线程 + 真 worktree 的单测易 flaky，不强加给本片）。Gemini 这里写**确定性单测**覆盖硬化逻辑：

  1. `test_merge_into_parent_conflict_detection`：构造最小 ctx（真实 `threading.Lock` 作 `subagent_lock`、`subagent_claims={}`、`tool_context.workspace=<parent>`）；在 `<merge_root>` 放改动文件，**顺序**调 `_merge_into_parent`：
     - 改不同文件（不同 label）→ 两次都进 `applied`、`ctx.subagent_claims` 含两文件、父工作区两文件都在；
     - 同一文件、不同 label → 第二次进 `conflicts`、父工作区保留**先到者**版本（先到先得不被破坏）。
  2. `test_sub_run_id_uses_uuid`：monkeypatch `xhx_agent.orchestrators.subagent.uuid.uuid4` 返回固定值，确认 `run_write_subagent` 生成的 worktree/分支名含该 uuid 片段、**不依赖 `len(ctx.subagent_claims)`**（即两次调用即便 claims 为空也得到不同名——通过注入不同 uuid 验证）。

  > `subagent_lock` 必须是真实 `threading.Lock`（不是 MagicMock），否则 `with ctx.subagent_lock:` 测不出锁语义。构造 ctx 可参考 `tests/test_subagent.py` 既有用例（MagicMock + 真实 workspace + 真实 lock/claims）。

- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 提交** —— `git commit -m "feat(subagent): uuid run-id + lock git/merge for parallel write sub-agents"`

---

### Task 3: DAGScheduler 每波全并行 + max_workers

**Files:** Modify `src/xhx_agent/planner/planner.py`；Test `tests/test_planner.py`

**实现：**
1. `execute` 签名加参数：`def execute(self, plan: DAGPlan, execute_node_callback, max_workers: int = 8) -> bool:`
2. 删掉 `_is_write` + 读/写分支（现 82-124 行那段），改为「每波所有就绪节点进一个池」：
```python
            workers = min(len(ready_nodes), max_workers)
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(execute_node_callback, node): node for node in ready_nodes}
                for future in concurrent.futures.as_completed(futures):
                    node = futures[future]
                    try:
                        success, result_summary = future.result()
                    except Exception as e:
                        success, result_summary = False, f"Exception: {e}"
                    node.status = "success" if success else "failed"
                    node_status[node.node_id] = node.status
                    node.result = result_summary
```

- [ ] **Step 1: 改测试**（行为反转）—— 把 P2 的 `test_dag_scheduler_serializes_edit_nodes` 改名/改断言为并行：
```python
def test_dag_scheduler_runs_ready_nodes_in_parallel() -> None:
    # ... 同结构的并发计数器 ...
    assert run(make("explore")) == 2
    assert run(make("edit")) == 2      # P2b：edit 也并行（安全由 subagent 层保证）
```
  确认 `test_dag_scheduler_success_and_blocked`（线性 A→B→C、B 失败→C blocked）仍通过（线性依赖每波一个，结果不变）。
- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 提交** —— `git commit -m "feat(graph): DAGScheduler runs all ready nodes in parallel (max_workers)"`

---

### Task 4: config + graph 接线（封顶并发度）

**Files:** Modify `src/xhx_agent/runtime/config.py`、`src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

**实现：**
1. config 加：`max_parallel_subagents: int = 4  # graph DAG 并发执行的子 agent 数上限，防嵌套 LLM 调用烧 token/撞限流`
2. graph 的 execute 节点里改调度调用：
```python
            from xhx_agent.runtime.config import load_config
            max_workers = load_config(ctx.original_workspace).max_parallel_subagents
            dag_ok = DAGScheduler(ctx.original_workspace).execute(plan, _cb, max_workers=max_workers)
```

- [ ] **Step 1: 写失败测试**（`tests/test_graph_orchestrator.py`）—— 两个无依赖 **edit** 节点并行（Barrier 证明）：
```python
def test_graph_runs_independent_edit_nodes_in_parallel(tmp_path, monkeypatch):
    """两个无依赖 edit 节点应并发执行（Barrier 证明）。"""
    import threading
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    barrier = threading.Barrier(2, timeout=5)
    done = []

    def fake_run_write_subagent(ctx, description, prompt, turn):
        barrier.wait()
        done.append(prompt)
        return f"edited:{prompt}", []

    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    class FakeClient:
        def chat(self, messages, tools):
            if "PLANNER" in messages[0]["content"]:
                return ChatResult(content=None, tool_calls=[ToolCall(id="p1", name="submit_dag", arguments={"nodes": [
                    {"id": "n1", "agent_type": "edit", "prompt": "edit A", "deps": []},
                    {"id": "n2", "agent_type": "edit", "prompt": "edit B", "deps": []},
                ]})])
            return ChatResult(content="synthesized")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("two edits", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert sorted(done) == ["edit A", "edit B"]   # 都越过 barrier == 真并行
```
- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 全量回归 + ruff**
  - `python -m pytest -q`（全绿）
  - `ruff check src/xhx_agent/orchestrators/base.py src/xhx_agent/orchestrators/subagent.py src/xhx_agent/planner/planner.py src/xhx_agent/runtime/config.py src/xhx_agent/orchestrators/graph.py tests/`（干净）
- [ ] **Step 6: 提交** —— `git commit -m "feat(graph): parallel edit nodes capped by max_parallel_subagents"`

---

## Self-Review（Gemini 自查）

- **并发安全是验收核心**：Task 2 的冲突检测确定性测试（不同文件都合并 / 同文件先到先得）+ uuid 测试必须绿；真·并行 worktree 竞态由 Claude 真模型 gate 兜底。
- **edit 真并行**：Task 4 Barrier 测试 + Task 3 max==2 测试绿。
- **锁范围正确**：只锁 worktree 创建/清理 + `_merge_into_parent` 三段；LLM 写循环在锁外（否则 edit 退化成串行，失去并行意义——自查这点）。
- **uuid 防撞**：`sub_run_id` 不再用 `len(claims)`。
- **explore 不回退**：原 explore 并行测试仍绿。
- **无多余锁**：除点名三处外没加别的锁。

## 交接说明（给 Claude 验收）

- Claude 验收：两段式审查（重点看锁范围 + 竞态测试是否真覆盖 race）+ `pytest -q` 全量 + **真 DeepSeek 联调跑一个"并行改 2 个独立文件"的任务**，确认两 edit 真并发、两文件都改对、无串改/丢改。
- 风险最高的是 git worktree 并发；若真模型联调出现 worktree 锁报错，回看 Task 2 的三段锁是否都生效。
- 相关：spec、P1/P2 计划同目录、[[roadmap-direction]]。
