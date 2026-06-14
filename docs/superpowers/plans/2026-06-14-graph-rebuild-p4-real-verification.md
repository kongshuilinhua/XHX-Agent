# graph 重铸 P4：真实验证集成（joiner 看真测试结果）Implementation Plan

> **For agentic workers (Gemini):** 自包含、可冷启动。按 Task 顺序 TDD。**这片是 graph 重铸收尾：把 P3 的 replan 回路从"LLM 主观判定"升级为"真实测试结果驱动"**——DAG 执行产生改动后跑真实验证（复用 plan 的同一套机制），测试挂了就把失败回喂 planner 重规划纠偏（有界），补齐 graph 相对 plan 的最后短板。完成交回，Claude 两段式审查 + 全量回归 + 真模型联调（含一次"改坏→验证挂→修复→通过"）+ 合并。

**Goal:** graph 执行完 DAG 后**跑真实验证**（infer_verification + kernel.run_verification，与 plan 完全同源），结果喂给收尾决策：测试失败→在 P3 的有界 replan 回路里**纠偏重做**（planner 收到失败输出产出修复 DAG）；测试通过/无可验证→joiner 正常收尾。失败且修不好时如实上报 `verification="failed"` + restore_plan（可回滚）。

**Architecture:** 在 P3 控制流 `planner→execute→joiner` 之间插入 `verify` 节点：
```
entry → planner ──answer_user──────────────────────────────→ END
            │
            └─submit_dag→ execute → verify ──(测试挂&可修)→ planner（带失败反馈）
                            ▲                  │
                            │                  └─(通过/无验证/修复预算耗尽)→ joiner ──finish──→ END
                            └───────────────────────────────────────────────replan──┘
```
- `verify` 节点：对本运行累计 `changed_files` 跑真实验证（无改动→`skipped_no_changes`）；建 checkpoint；产出 `verification`(passed/failed/...) + `verification_results` + 失败摘要，写入 state。
- `route_after_verify`：`verification=="failed"` 且 `replan_count < max_graph_replans` 且 `ctx.auto_repair` → **"repair"**（把失败摘要塞进 `joiner_feedback`、`replan_count+1`、回 planner 出修复 DAG）；否则 → **"judge"**（进 joiner）。
- joiner（P3 已有）：summary 里**带上 verification 状态/失败摘要**，据此 finish/replan；它仍可对"答案不充分"replan（P3 行为不变）。
- **run status 语义（沿用 plan，关键决策）**：`status`= DAG 是否跑通（dag_ok），**与测试是否通过解耦**；测试结果走独立的 `RunResult.verification`。原因：`run_task` 仅在 `status=="success"` 时把顶层 worktree 改动 sync 回真仓库（[app.py](../../../src/xhx_agent/runtime/app.py) 约 214 行）——若测试挂就把 status 打成 failed，改动会被整体丢弃、用户拿不到任何产物且没有 restore_plan，反而比 plan 弱。所以"测试挂"= `status="success"(DAG 跑通) + verification="failed" + restore_plan`，和 plan 一致。

**Tech Stack:** 复用 plan 的验证栈——`verification/router.py::infer_verification`、`kernel.run_verification/create_checkpoint/create_restore_plan`、`runtime/verify_loop.py::{checkpoint_path_value, restore_plan_path_value, _refresh_repo_intel_index}`、`safety/repair.py::decide_repair`；LangGraph 节点/条件边；pytest（含真实 git 仓库 + 真实 pytest 子进程的 e2e）。

**前置：** P1（tool-calling planner）、P2/P2b（并行 DAG）、P3（joiner + 有界 replan + edit 跨轮播种）均已合并到 main（最新 `78b7c4a`）。

---

## 范围与边界

- **本计划 P4 = 真实验证集成 + 测试驱动的有界修复 + checkpoint/restore 上报**。完成后 graph 与 plan 在"真实验证 + 有界自修复"上达到 parity，且修复跑在并行多 agent DAG 上。
- **不在本计划**：新的验证种类（沿用 infer_verification 现有 python/node 推断，不扩）；token 计量（另有缺口，与本片无关）。
- **不做过度**：① 验证只在 `ctx.workspace`（顶层 worktree，改动 merge 落点）跑，**别**在 `original_workspace`/子 agent worktree 跑；② **不**改 run status=DAG 语义（见上，测试挂不打 failed）；③ 不动 P3 的 joiner/replan/播种逻辑，只在其前面插 verify 并接线；④ 修复预算复用 `max_graph_replans`（不新增 knob），auto_repair 复用 `ctx.auto_repair`（不新增 flag）。
- **行为提醒**：现有 graph run() 里 `verification_status = "not_executed" if changed_files else "skipped_no_changes"`（约 [graph.py:301]）是占位，本片**替换**为真实验证结果。检查所有断言 `result.verification == "not_executed"` 的现有测试（若有）并迁移。

## 关键设计分析（先读懂再动手）

1. **验证落点**：`run_task` 给所有模式套顶层 worktree，`ctx.workspace == ctx.tool_context.workspace == ctx.kernel.workspace ==` 顶层 worktree（改动 merge 落点），`ctx.original_workspace ==` 真仓库。所以验证**必须用 `ctx.workspace`**：`infer_verification(ctx.workspace, changed)` + `ctx.kernel.run_verification(...)`（kernel 已指向顶层 worktree）。这与 plan 的 `_verify_and_repair` 完全一致——照抄即可。
2. **修复轮与 P3 播种的协同**：测试挂→回 planner→execute 再跑。execute 入口（P3）`claims.clear()` + 把累计 changed 作 `seed_files` 播种进 edit 子 agent 的 worktree → 修复轮的 edit 在"上一轮改动 + 失败代码"之上续改 → 再 verify。**这正是 plan repair loop 的 graph 版**，地基 P3 已铺好，本片只接 verify。
3. **status 与 sync 的耦合**：见上"run status 语义"。务必保持 `status` 反映 DAG 执行；verification 独立上报；失败配 restore_plan。
4. **预算统一**：测试驱动 repair-replan 与 P3 的 joiner-insufficiency-replan **共享** `max_graph_replans` 预算（同一个 `replan_count`）。repair 额外要 `ctx.auto_repair` 门控（对齐 plan：自动改代码修测试是 opt-in）；joiner-replan 不门控（P3 行为不变）。

## File Structure

- `src/xhx_agent/orchestrators/graph.py` — 加 `verify` 节点 + `route_after_verify`；`_GraphState` 加验证字段；joiner summary 带验证；run() 用真实 verification + checkpoint/restore 填 RunResult。
- 测试：`tests/test_graph_orchestrator.py`（验证执行/跳过/失败上报、测试驱动 repair e2e、status 语义）。
- **只读不改**（理解现状）：`src/xhx_agent/orchestrators/plan.py::_verify_and_repair`（范本）、`src/xhx_agent/verification/router.py`、`src/xhx_agent/safety/kernel.py`(verification 段)、`src/xhx_agent/runtime/verify_loop.py`、`src/xhx_agent/safety/repair.py`、`src/xhx_agent/runtime/app.py`(214 行 sync 逻辑)。

---

### Task 1: verify 节点 —— 跑真实验证 + 喂 joiner + RunResult 上报（先不接 repair 路由）

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

**实现：**

1. **`_GraphState` 加字段**：
```python
    verification: str                       # passed | failed | requires_confirmation | not_executed | skipped_no_changes
    verification_results: list[Any]         # list[TerminalResult]
    commands_run: list[str]
    verification_failure: str | None        # 失败摘要（喂 planner/joiner）
    checkpoint: Any | None                  # 最近一次 verify 的 Checkpoint（供 run() 生成 restore_plan）
    repair_attempts: int
```
invoke 初值 + 异常兜底 final 都补：`"verification": "skipped_no_changes", "verification_results": [], "commands_run": [], "verification_failure": None, "checkpoint": None, "repair_attempts": 0`。

2. **`verify` 节点**（照抄 plan `_verify_and_repair` 的单轮验证段，去掉 plan 自己的 while/repair——repair 由 graph 的 replan 回路承担）：
```python
        def verify(state: _GraphState) -> dict[str, Any]:
            from xhx_agent.runtime.verify_loop import _refresh_repo_intel_index
            from xhx_agent.verification.router import infer_verification

            changed = sorted(set(state["changed_files"]))
            if not changed:
                return {"verification": "skipped_no_changes"}

            _refresh_repo_intel_index(ctx.workspace, ctx.evidence, ctx.event_callback, [])
            vplan = infer_verification(ctx.workspace, changed)
            if not vplan.commands:
                return {"verification": vplan.skip_reason or "not_executed"}

            checkpoint = ctx.kernel.create_checkpoint(changed)
            emit_event(ctx.event_callback, "checkpoint", "Checkpoint created.",
                       checkpoint_id=checkpoint.id, changed_files=changed)

            results: list[Any] = []
            cmds: list[str] = []
            ok = True
            requires_confirmation = False
            for cmd in vplan.commands:
                er = ctx.kernel.run_verification(
                    cmd.command, assume_yes=ctx.assume_yes,
                    confirm_callback=ctx.confirm_callback, event_callback=ctx.event_callback)
                cmds.append(cmd.command)
                results.append(er)
                if er.status == "confirm":
                    requires_confirmation = True; ok = False; break
                if er.status != "success":
                    ok = False
            verification = ("passed" if ok else
                            "requires_confirmation" if requires_confirmation else
                            "failed" if any(r.status == "failed" for r in results) else "not_executed")
            failure = None
            if verification == "failed":
                failure = next((r.stderr or r.stdout or r.summary for r in results
                                if r.status == "failed" and (r.stderr or r.stdout or r.summary)), "tests failed")
            emit_event(ctx.event_callback, "graph_verify",
                       f"Verification: {verification}.", verification=verification)
            return {"verification": verification, "verification_results": results,
                    "commands_run": cmds, "verification_failure": failure, "checkpoint": checkpoint}
```

3. **接线**（Task 1 先 verify 总是 → joiner，repair 路由留 Task 2）：
```python
        graph.add_node("verify", verify)
        graph.add_edge("execute", "verify")
        graph.add_edge("verify", "joiner")
```
（删掉 P3 的 `graph.add_edge("execute", "joiner")`。）

4. **joiner summary 带验证**（在 P3 joiner 的 summary 末尾追加）：
```python
            vstat = state.get("verification", "skipped_no_changes")
            summary += f"\n\nVerification result: {vstat}"
            if state.get("verification_failure"):
                summary += f"\nVerification failure output:\n{state['verification_failure'][:1500]}"
```
并在 `JOINER_PROMPT` 末尾加一句：`"If a verification result is provided, weigh it: do not finish claiming success when verification failed unless it cannot be fixed."`

5. **run() 用真实结果填 RunResult**：删除占位 `verification_status = "not_executed" if changed_files else "skipped_no_changes"`；改用 `final.get("verification", "skipped_no_changes")`、`final.get("verification_results", [])`、`final.get("commands_run", [])`，传给 `write_report(... verification=..., verification_results=...)` 与 `RunResult(... verification=..., verification_results=..., commands=...)`。

- [ ] **Step 1: 写测试**（`tests/test_graph_orchestrator.py`）
  1. `test_graph_runs_real_verification_on_changes`：真实 git 仓库 + 一个真能跑的最小 python 项目（`tests/` 下放一个 `test_ok.py: def test_x(): assert True`）。FakeClient：planner→单 edit 节点（改一个 .py 文件，触发 python 验证）；用真实 `run_write_subagent`（fake child client 用 apply_patch 真改）或 fake `run_write_subagent` 返回 changed=["something.py"] 并真在 workspace 写该文件——**但验证跑的是 ctx.workspace（顶层 worktree）**，所以更稳的做法：让 edit 真在顶层 worktree 产文件（用真 `run_write_subagent` + fake child）。joiner→finish。断言 `result.verification == "passed"`、`result.commands` 含 `pytest`。
     > 若真 pytest 子进程在 CI 慢/不稳，可退而求其次：monkeypatch `graphmod` 引用的 `infer_verification` 返回一个无害命令（如 `python -c "import sys;sys.exit(0)"`）验证"链路跑通+状态映射"，把"真 pytest"交 Claude 真模型 gate。**二选一，优先真 pytest，不稳则降级并注明。**
  2. `test_graph_verification_skipped_when_no_changes`：explore-only DAG（无 changed）→ `result.verification == "skipped_no_changes"`、joiner 照常 finish。
  3. `test_graph_verification_failed_surfaces`：edit 产生改动、验证命令返回失败（monkeypatch `infer_verification` 给一个必失败命令如 `python -c "import sys;sys.exit(1)"`，或让 fake child 写一个会 fail 的测试）；joiner 这一测里直接 finish（不 replan）→ 断言 `result.verification == "failed"`、`result.status == "success"`（DAG 跑通，沿用 plan 语义）、`result.verification_results` 非空。
- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 提交** —— `git commit -m "feat(graph): run real verification after execute and feed result to joiner"`

---

### Task 2: 测试驱动的有界修复路由 + checkpoint/restore 上报

**Files:** Modify `src/xhx_agent/orchestrators/graph.py`；Test `tests/test_graph_orchestrator.py`

**实现：**

1. **`route_after_verify` + 改接线**：
```python
        def route_after_verify(state: _GraphState) -> str:
            from xhx_agent.runtime.config import load_config
            can_replan = state["replan_count"] < load_config(ctx.original_workspace).max_graph_replans
            if state.get("verification") == "failed" and can_replan and ctx.auto_repair:
                return "repair"
            return "judge"

        graph.add_conditional_edges("verify", route_after_verify, {"repair": "planner", "judge": "joiner"})
```
（删掉 Task 1 的 `graph.add_edge("verify", "joiner")`。）

2. **verify 节点产出 repair 反馈**：当 `verification=="failed"`，把失败摘要也写进 `joiner_feedback` 并 `replan_count+1`、`repair_attempts+1`——但**只在真的会走 repair 时**才加计数，否则 route 判不到 repair 会错乱。最简洁做法：把"是否 repair"的判断集中在 `route_after_verify`，而 verify 节点**总是**把失败摘要暴露在 `verification_failure`；planner 节点（P3）读 `joiner_feedback`，所以 repair 路径要让 planner 拿到失败。方案：**verify 节点在 failed 时设 `joiner_feedback = 失败摘要`**（无害；joiner 路径不依赖它），并在 route 判定走 repair 时由一个轻量 `repair` 透传节点 `+1` 计数——

   > **更简方案（推荐，避免额外节点）**：让 `route_after_verify` 只路由；把 `replan_count+1` / `repair_attempts+1` / `joiner_feedback=失败摘要` 放进 **verify 节点**里，仅当"将要 repair"（`failed and can_replan and auto_repair`）时设置。即 verify 节点末尾：
```python
            result_update = {"verification": verification, "verification_results": results,
                             "commands_run": cmds, "verification_failure": failure, "checkpoint": checkpoint}
            from xhx_agent.runtime.config import load_config
            will_repair = (verification == "failed"
                           and state["replan_count"] < load_config(ctx.original_workspace).max_graph_replans
                           and ctx.auto_repair)
            if will_repair:
                result_update.update({
                    "joiner_feedback": f"Verification FAILED. Fix the code so tests pass.\n{failure}",
                    "replan_count": state["replan_count"] + 1,
                    "repair_attempts": state["repair_attempts"] + 1,
                })
                emit_event(ctx.event_callback, "graph_repair",
                           f"Verification failed; repairing (attempt {state['repair_attempts'] + 1}).")
            return result_update
```
   然后 `route_after_verify` 复用同一条件（或读 `state` 里刚 +1 的 `repair_attempts` 不可靠——LangGraph 节点返回的 update 在 route 时已合并，但为稳妥**让 route 重新独立判断**同一条件，二者一致即可）。
   > 注意：planner（P3）读 `joiner_feedback` 出纠偏 DAG——repair 走 planner 正好复用。verify 设的 feedback 措辞强调"修复使测试通过"。

3. **run() 生成 restore_plan + 填 RunResult**（照抄 plan 收尾）：
```python
        from xhx_agent.runtime.verify_loop import checkpoint_path_value, restore_plan_path_value
        from xhx_agent.safety.repair import decide_repair

        verification = final.get("verification", "skipped_no_changes")
        verification_results = final.get("verification_results", [])
        commands_run = final.get("commands_run", [])
        repair_attempts = final.get("repair_attempts", 0)
        checkpoint = final.get("checkpoint")
        repair_decision = decide_repair(verification, attempts_used=repair_attempts, auto_repair_enabled=ctx.auto_repair)

        checkpoint_path = restore_plan_path = None
        if checkpoint is not None:
            checkpoint_path = str(checkpoint_path_value(ctx.original_workspace, ctx.run_id))
            if verification == "failed":
                ctx.kernel.create_restore_plan(checkpoint)
                restore_plan_path = str(restore_plan_path_value(ctx.original_workspace, ctx.run_id))
                emit_event(ctx.event_callback, "restore_plan", "Restore plan created.", run_id=ctx.run_id)
```
   把 `verification/verification_results/commands_run/checkpoint_path/restore_plan_path/repair_decision/repair_attempts` 都传进 `write_report(...)` 与 `RunResult(...)`（字段名见 [app.py RunResult](../../../src/xhx_agent/runtime/app.py)：`verification, verification_results, checkpoint_path, restore_plan_path, repair, repair_attempts`）。**status 维持 P3 的 dag_ok 语义，不因 verification 改。**

- [ ] **Step 1: 写测试**（`tests/test_graph_orchestrator.py`）
  1. `test_graph_repairs_on_verification_failure`（**核心 e2e**，真实 git + 真 pytest）：项目里放一个测试 `tests/test_target.py` 断言某模块函数返回值。FakeGraphClient：planner 第一次产 edit 节点写一个**会让测试挂**的实现、joiner 不参与（因为 verify 会直接 repair）；planner 第二次（repair 轮，收到 `joiner_feedback` 含 "Verification FAILED"）产 edit 节点写**正确实现**；第二次 verify 通过→joiner finish。`run_task(..., mode="graph", assume_yes=True, auto_repair=True)`。断言：`result.verification == "passed"`、planner 被调 2 次、`result.repair_attempts == 1`、最终文件是修复版。
     > child client 用 apply_patch 真改；靠 P3 播种让 repair 轮在第一轮代码上续改。这条同时验证 P4 repair 回路 + P3 播种在 verify 驱动下成立。
  2. `test_graph_no_repair_when_auto_repair_off`：同上但 `auto_repair=False`（默认）→ verify failed 后**不**repair，直接 joiner finish；断言 `result.verification == "failed"`、planner 只调 1 次、`result.restore_plan_path` 非 None、`result.status == "success"`。
  3. `test_graph_repair_budget_exhausted`：`auto_repair=True` 但每轮实现都让测试挂 → repair 到 `max_graph_replans` 上限停 → 最终 `verification == "failed"` + restore_plan；断言 `result.repair_attempts == max_graph_replans`、planner 调 `max_graph_replans+1` 次、不死循环。
  4. （若 Task 1 用了 monkeypatch 降级版，这里至少 1 条用**真 pytest**跑通 repair，确保不是纸面逻辑。）
- [ ] **Step 2–4: 失败→实现→通过**
- [ ] **Step 5: 全量回归 + ruff**
  - `PYTHONUTF8=1 uv run pytest -q`（全绿；基线起点 448 passed/1 skipped，本片新增测试后通过数增加）
  - `PYTHONUTF8=1 uv run ruff check src/xhx_agent/orchestrators/graph.py tests/test_graph_orchestrator.py`（干净）
- [ ] **Step 6: 提交** —— `git commit -m "feat(graph): test-driven bounded repair loop + checkpoint/restore reporting"`

---

## Self-Review（Gemini 自查）

- **验证落点对**：用 `ctx.workspace`（顶层 worktree）+ `ctx.kernel.run_verification`，不是 original_workspace。
- **status 语义对**：测试挂 → `status=="success"`(DAG 跑通) + `verification=="failed"` + restore_plan，**不**把 status 打成 failed（否则改动被 app.py 丢弃、用户两手空）。
- **repair 有界**：budget 耗尽测试绿；repair 复用 `max_graph_replans` + `replan_count`，门控 `ctx.auto_repair`；`route_after_verify` 与 verify 节点里的 `will_repair` 条件**一致**。
- **修复真生效**：核心 e2e 用真 pytest，第二轮在第一轮代码上修好（靠 P3 播种），`verification` 由 failed→passed。
- **P3 不回退**：joiner/replan/播种逻辑未改；joiner 仍能对答案不充分 replan；现有 P3 测试全绿。
- **RunResult 完整**：verification/verification_results/commands/checkpoint_path/restore_plan_path/repair/repair_attempts 都来自真实运行，无占位。

## 交接说明（给 Claude 验收）

- **两段式审查**重点：① 验证用 `ctx.workspace`；② status≠verification（测试挂不打 failed、配 restore_plan）；③ `will_repair` 与 `route_after_verify` 条件一致、预算复用 `max_graph_replans`、门控 `auto_repair`；④ run() 的 checkpoint/restore 照 plan 收尾。
- **全量回归**：`PYTHONUTF8=1 uv run pytest -q`。
- **真 DeepSeek 联调 gate（Claude 亲跑）**：
  1. **改对→验证通过→finish**：一个有测试的小项目，让 graph 改一处使测试通过；确认 `verification=passed`、改动 sync 回真仓库。
  2. **改坏→验证挂→修复→通过**（最关键，`--auto-repair`/`auto_repair=True`）：故意让首轮改动使测试挂，确认 verify 触发 repair-replan、planner 收到失败、第二轮在第一轮代码上修好（验 P3 播种）、`verification` 终为 passed、`repair_attempts>=1`、无 worktree/分支残留。
  3. **修不好→如实上报**：预算耗尽仍挂 → `verification=failed` + restore_plan 存在 + 不死循环。
- 风险点：repair 轮的 edit 看不看得到上一轮失败代码（P3 播种 × verify 落点的协同）；若真模型联调出现"第二轮从干净 HEAD 重来修不到点上"，回看播种（`seed_files`=累计 changed）与验证（`ctx.workspace`）是否同一落点。
- 相关：spec `docs/superpowers/specs/2026-06-14-graph-llmcompiler-rebuild-design.md`(§7 P4)、plan `_verify_and_repair`（范本）、P1/P2/P2b/P3 计划同目录、[[roadmap-direction]]、[[real-llm-testing]]。**P4 完成 = graph 重铸 P1–P4 全收官。**
