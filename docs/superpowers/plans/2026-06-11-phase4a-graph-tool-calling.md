# 计划（精简）：Phase 4a `graph` 范式迁 tool-calling —— 测试

> 精简计划（见 `docs/superpowers/gemini-handoff-workflow.md`）。**核心已由 Claude 写好（commit `3e20168`）；你的活 = 写测试**，不要重写核心（如发现核心 bug，最小修正并说明）。

## Goal
为新的 tool-calling `graph` 范式补测试。graph 现在是**真 LLM 多 agent 工作流**（LangGraph：coordinator → execute → review → 条件重试），完成"三范式（loop/plan/graph）都用原生 tool-calling"的核心叙事。

## 现状：核心已由 Claude 完成（commit `3e20168`）
- `src/xhx_agent/orchestrators/graph.py` 重写：
  - `_coordinate(ctx, client)`：LLM 把任务拆成子任务列表（解析 `- ` 行；解析不出→`[task]`；上限 `MAX_SUBTASKS=5`）。
  - `_run_worker(ctx, client, subtask, turn)`：**写型 worker 小循环**，受限工具 `WORKER_TOOLS={search,read_file,apply_patch}`，tool-calling 真改代码，限轮 `WORKER_MAX_TURNS=4`，返回 `(changed_files, 结果文本)`。
  - `_review(ctx, client, changed, results)`：LLM 评审，返回 `(passed, reason)`（`PASS` / `FAIL: ...`）。
  - `GraphOrchestrator.run`：LangGraph 串起来，`route` 在 review 通过或 `rounds>=min(MAX_REVIEW_ROUNDS, max_loop_turns)` 时收尾，否则回 execute 重试。emit `graph_coordinator`/`graph_worker`/`graph_execute`/`graph_review`。
  - **三个角色共用同一个 `client`**（`build_chat_client(ctx.profile)` 建一次）；测试里 fake 按 `messages[0]["content"]` 含 `COORDINATOR`/`WORKER`/`REVIEWER` 区分角色。
- `tests/test_orchestrators.py::test_graph_mode_runs_via_langgraph` 已由 Claude 迁到新范式（**就是 fake-by-role 的可照搬范例**）。现状 **329 passed, 1 skipped, ruff 绿**；Claude 内联冒烟过 PASS 路径（coordinator→worker apply_patch 真建文件→reviewer PASS→success）与 FAIL 路径（reviewer FAIL→重试→触顶→failed）。

## 先读
- `src/xhx_agent/orchestrators/graph.py`（核心）。
- `tests/test_orchestrators.py::test_graph_mode_runs_via_langgraph`（**fake-by-role 范例，照它写**）。
- `tests/test_subagent.py` / `tests/test_plan_orchestrator.py`（fake chat / run_task 写法参考）。

## 边界（不许动）
- 不改 `graph.py` 行为契约、不改其他编排器。
- 现有测试保持全绿。

## 关键 check 点（每个要有测试；建议新建 `tests/test_graph_orchestrator.py`）
1. **coordinator 解析**：`_coordinate` 把 `"- a\n- b"` 解析成 `["a","b"]`；无 `- ` 行时回退 `[ctx.task]`；超过 5 条被截到 `MAX_SUBTASKS`。（可用一个返回固定 content 的 fake client + 直接调 `_coordinate`，或经 `run_task` 断言 `graph_worker` 事件数。）
2. **worker 真改代码**：fake worker 发 `apply_patch` → 经 `run_task(mode="graph")` 后 `changed_files` 含目标文件、文件内容已改。
3. **reviewer FAIL→重试→PASS**：stateful fake 让 reviewer 第一轮 `FAIL: x`、第二轮 `PASS` → `result.turns==2`、`status=="success"`。
4. **触顶失败**：reviewer 恒 `FAIL` → `status=="failed"`、`turns==2`(=MAX)、`risk_summary` 含失败原因。
5. **多子任务**：coordinator 返回 2 个子任务 → 跑 2 个 worker（断言 2 个 `graph_worker` 事件，或两处改动）。
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（≥329 + 新测试）、ruff 绿。

## 纪律 / 明确排除
- TDD；命令前置 `PYTHONUTF8=1`；每步零回归 + ruff 绿；只在分支提交，不 push main，commit 只 add 测试文件（核心已提交）。
- **不做**：dag 并发执行层（子任务目前串行，并发吸收留后续）、验证命令集成、真模型调优、worker 加更多工具。
