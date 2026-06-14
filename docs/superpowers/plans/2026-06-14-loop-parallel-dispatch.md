# loop 并行 dispatch（Level 1a：并发 explore 子 agent）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 loop 在一轮里收到多个 `dispatch(agent_type='explore')` 时**并发**执行这些只读子 agent（而非现在的串行），把"并行调查 N 个模块"做成真正的并行。

**Architecture:** 复用 loop 已有的"只读工具批量并发"路径——把判定从"全部只读"放宽到"全部并行安全"，其中 explore 子 agent（只读、隔离、不碰父工作区共享写状态）视为并行安全；edit（写）子 agent 仍走串行，留给 Slice B 单独硬化。不引入新基础设施，零新依赖。

**Tech Stack:** Python、`concurrent.futures.ThreadPoolExecutor`（loop 已用）、pytest、`threading.Barrier`（并发证明）。

---

## 范围与边界

- **本计划只做 Slice A**：并发 **explore（只读）** dispatch。这是安全的——explore 子 agent 经 `run_subagent` 只用 `search/read_file`，不建 worktree、不合并、不写父工作区共享状态，并发风险等级与 loop 现有"多个只读 tool call 并发"完全相同（那条路径已在生产/测试中验证）。
- **不在本计划内（→ Slice B 另开计划）**：并发 **edit（写）** dispatch。它有真实并发隐患，必须单独处理：
  1. `WorktreeContext.__enter__` 跑 `git worktree add`（在主仓库），并行创建有 git 锁争用风险；
  2. `subagent.py` 的 `sub_run_id = f"{ctx.run_id}-edit{turn}-{len(ctx.subagent_claims)}"` 用 `len(claims)` 取唯一值，并行下会算出相同 id → worktree 路径冲突（应改 uuid）；
  3. `_merge_into_parent` 读写共享 `ctx.subagent_claims` + 拷文件进父工作区，是临界区，需加锁（`OrchestratorContext` 加 `subagent_lock`）。
- **已知次要项（不阻塞本计划）**：并发子 agent 各自调 `chat_and_count`，会并发 `+=` `ctx.metrics_tracker["tokens"]`，存在丢更新的竞态 → token 估算可能略偏小。非破坏性；与现有只读并发同源。留作后续 metrics 硬化。

## File Structure

- `src/xhx_agent/orchestrators/loop.py` — 唯一改动文件：把工具批执行的并发判定从 `_is_readonly` 放宽到 `_is_parallel_safe`（含 explore dispatch）。职责不变，仅放宽并发条件。
- `tests/test_loop_orchestrator.py` — 新增一个并发证明测试（`threading.Barrier`：串行会超时凑不齐，唯有真并行能通过）。

---

### Task 1: 放宽 loop 的并发判定以涵盖 explore dispatch

**Files:**
- Modify: `src/xhx_agent/orchestrators/loop.py:140-153`（`_is_readonly` / `all_readonly` 那段）
- Test: `tests/test_loop_orchestrator.py`

- [ ] **Step 1: 写失败测试（并发证明）**

在 `tests/test_loop_orchestrator.py` 末尾追加：

```python
def test_loop_runs_explore_dispatch_batch_in_parallel(tmp_path, monkeypatch):
    """一轮里多个 explore dispatch 应并发执行。

    用 Barrier(2) 证明并行：两个子 agent 必须同时到达 barrier 才能越过；
    若串行执行，第一个会一直等到 5s 超时触发 BrokenBarrierError，两个都越不过，completed 为空。
    """
    import threading

    import xhx_agent.orchestrators.loop as loopmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    barrier = threading.Barrier(2, timeout=5)
    completed: list[str] = []

    def fake_run_subagent(ctx, *, description, prompt, agent_type="explore", turn=0):
        barrier.wait()  # 仅当两个线程同时到达才返回；串行 → 超时 BrokenBarrierError
        completed.append(prompt)
        return f"[sub-agent explore] {prompt}"

    monkeypatch.setattr(subagentmod, "run_subagent", fake_run_subagent)

    class FakeClient:
        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return ChatResult(content=None, tool_calls=[
                    ToolCall(id="d1", name="dispatch",
                             arguments={"prompt": "explore A", "agent_type": "explore"}),
                    ToolCall(id="d2", name="dispatch",
                             arguments={"prompt": "explore B", "agent_type": "explore"}),
                ])
            return ChatResult(content="done")

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeClient())

    result = RuntimeApp(tmp_path).run_task("investigate two modules", assume_yes=True, mode="loop")

    assert result.status == "success"
    # 两个 explore 都越过 barrier == 真并行；串行时 completed 为空、断言失败
    assert sorted(completed) == ["explore A", "explore B"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_loop_orchestrator.py::test_loop_runs_explore_dispatch_batch_in_parallel -v`
Expected: FAIL —— 当前 dispatch 非只读，走串行分支，第一个 `barrier.wait()` 5s 超时抛 `BrokenBarrierError`（被 dispatch 的 try/except 吞成错误字符串），`completed` 为空 → 断言 `sorted(completed) == [...]` 失败。

- [ ] **Step 3: 放宽并发判定**

在 `src/xhx_agent/orchestrators/loop.py` 把这段（约 140-153 行）：

```python
            reg = ctx.kernel.tool_registry

            def _is_readonly(tc, reg=reg) -> bool:
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_readonly = len(result.tool_calls) >= 2 and all(_is_readonly(tc) for tc in result.tool_calls)
            if all_readonly:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_run, result.tool_calls))
            else:
                outcomes = [_run(tc) for tc in result.tool_calls]
```

改成：

```python
            reg = ctx.kernel.tool_registry

            def _is_parallel_safe(tc, reg=reg) -> bool:
                # explore 子 agent 只读且隔离，可与只读工具一起并发；
                # edit(写) 子 agent 有 worktree/合并并发隐患，仍走串行（Slice B 单独处理）。
                if tc.name == "dispatch":
                    return str(tc.arguments.get("agent_type", "explore")) != "edit"
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_parallel_safe = len(result.tool_calls) >= 2 and all(_is_parallel_safe(tc) for tc in result.tool_calls)
            if all_parallel_safe:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_run, result.tool_calls))
            else:
                outcomes = [_run(tc) for tc in result.tool_calls]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_loop_orchestrator.py::test_loop_runs_explore_dispatch_batch_in_parallel -v`
Expected: PASS（两个 explore 子 agent 并发越过 barrier，`completed == ["explore A","explore B"]`）。

- [ ] **Step 5: 全量回归 + lint**

Run: `python -m pytest -q`
Expected: 全绿（基线 434 passed/1 skipped + 本测试 = 435 passed/1 skipped），无回归。
Run: `ruff check src/xhx_agent/orchestrators/loop.py tests/test_loop_orchestrator.py`
Expected: 干净。

- [ ] **Step 6: 提交**

```bash
git add src/xhx_agent/orchestrators/loop.py tests/test_loop_orchestrator.py
git commit -m "feat(loop): run explore dispatch batches in parallel (Level 1a)"
```

---

## Self-Review

- **Spec coverage**：目标=并发 explore dispatch。Task 1 改判定 + 并发证明测试，覆盖。edit 并发显式划到 Slice B（范围已声明）。
- **Placeholder scan**：无 TODO/占位，所有步骤含确切代码与命令。
- **Type consistency**：`_is_parallel_safe` 替换 `_is_readonly`，`all_parallel_safe` 替换 `all_readonly`，引用一致；`ChatResult`/`ToolCall` 签名与 `tests/test_graph_orchestrator.py` 既有用法一致；`dispatch` 参数 `agent_type` 与 `tools/registry.py` 定义一致。

---

## Appendix：后续切片（不在本计划执行范围，仅记录全貌）

按 [[roadmap-direction]] 2026-06-14 修订方向：

- **Slice B — 并发 edit(写) dispatch**：`OrchestratorContext` 加 `subagent_lock: threading.Lock`；`subagent.py` 的 `sub_run_id` 改 `uuid4` 去碰撞；`_merge_into_parent` 调用包在 `ctx.subagent_lock` 临界区；`git worktree add` 创建串行化（或验证 git 自带锁足够）；把 `_is_parallel_safe` 对 edit 也放行。需要专门的并发竞态测试（claim 冲突仍正确检测、无文件串改）。
- **graph 重铸（Level 2，LLMCompiler 式）**：分阶段——① LLM planner 出带 `$N` 依赖的 DAG；② `DAGScheduler` 接入 + **变量替换**（节点 `$N` 填前序结果，现 `dag_runner` 缺）；③ sub-agent 当 executor；④ joiner/replan 节点。参考 LLMCompiler(arXiv 2312.04511) / `pip install llmcompiler` / LangGraph 官方 llm-compiler 教程。
- **dag 模式退役**：删启发式 `DAGPlanner`、拆 `classifier.py` 的 `refactor/重构→dag` 路由（改落 loop），`DAGScheduler` 转为 graph 执行层。
