# 计划（精简）：Phase 8b tool-calling 编排器 token 计量 —— 测试

> 精简计划（见 `docs/superpowers/gemini-handoff-workflow.md`）。**核心已由 Claude 写好（commit `0a31391`）；你的活 = 写测试**，不要重写核心（如发现核心 bug，最小修正并说明）。

## Goal
补上 8a benchmark 的缺口：让 tool-calling 编排器（loop/plan/graph + 子 agent）累加 token 估算，benchmark 的 token 维度从此有意义、能区分范式。

## 现状：核心已由 Claude 完成（commit `0a31391`）
- `orchestrators/_toolturn.py`：新增 `_estimate_message_tokens(messages)`（复用 `context.compiler._estimate_tokens`，tiktoken + 字符回退）+ `chat_and_count(ctx, client, messages, schemas)`（调 chat 前把本轮上下文 token 累加进 `ctx.metrics_tracker["tokens"]`，与 legacy"每轮计整段上下文"语义一致）。
- 所有 chat 调用点改用 `chat_and_count`：`loop.py`、`plan.py`(`_drive`)、`graph.py`(`_coordinate`/`_run_worker`/`_review`)、`subagent.py`（子 agent token 计入父的共享 `ctx.metrics_tracker`）。
- `loop.py` / `plan.py` 现在**设置 `RunResult.metrics`**（`RunMetrics`，含 `tokens_estimate=ctx.metrics_tracker["tokens"]` + duration/turns/files/repair/success）；`graph.py` 早已设 metrics，累加后 token 自然有值。
- 现状 **337 passed, 1 skipped, ruff 绿**；Claude 内联冒烟过：mock 下 loop tokens≈417、plan≈424、benchmark 三范式 mean_tokens = graph 1262 / loop 424 / plan 433（graph 多 agent 开销最大，~3×，区分明显）。

## 先读
- `src/xhx_agent/orchestrators/_toolturn.py`（`chat_and_count` / `_estimate_message_tokens`）。
- `src/xhx_agent/orchestrators/loop.py`、`plan.py`（RunResult.metrics 现已设）。
- `tests/test_loop_orchestrator.py` / `tests/test_plan_orchestrator.py`（fake client 写法）。

## 边界（不许动）
- 不改核心行为契约；不改 benchmark.py（8a 已交付）。
- 现有测试全绿。

## 关键 check 点（每个要有测试）
1. **`_estimate_message_tokens` 正向**：对一组带 content 的消息返回 > 0；空消息返回 0；含 `tool_calls.arguments` 也计入。（纯函数单测，直接调。）
2. **`chat_and_count` 累加**：构造一个 ctx（`metrics_tracker={"tokens":0}`）+ fake client，调用后 `ctx.metrics_tracker["tokens"]` 增长，且返回值 == fake client 的返回。
3. **loop 设 metrics**：fake client 跑 `run_task(mode="loop")` 后 `res.metrics is not None` 且 `res.metrics.tokens_estimate > 0`。
4. **plan 设 metrics**：同上，`mode="plan"`。
5. **benchmark token 有值且区分**：mock 下 `run_matrix` + `render_benchmark_report`，断言至少一个 mode 的 `mean_tokens > 0`（可断言 graph 的 mean_tokens ≥ loop 的，体现多 agent 开销——若不稳可只断言 > 0）。
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（≥337 + 新测试）、ruff 绿、既有 loop/plan/graph/benchmark 测试不破。

## 纪律 / 明确排除
- TDD；命令前置 `PYTHONUTF8=1`；每步零回归 + ruff 绿；只在分支提交，不 push main，commit 只 add 测试文件（核心已提交）。
- **不做**：从真实 API `usage` 取精确 token（当前是 tiktoken 估算，够对比）、token 预算裁剪、报告图表。
