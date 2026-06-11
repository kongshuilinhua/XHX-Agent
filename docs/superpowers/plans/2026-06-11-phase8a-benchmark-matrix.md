# 计划（精简）：Phase 8a 三范式 benchmark 矩阵 —— 测试

> 精简计划（见 `docs/superpowers/gemini-handoff-workflow.md`）。**核心已由 Claude 写好（commit `1e5bd73`）；你的活 = 写测试**，不要重写核心（如发现核心 bug，最小修正并说明）。

## Goal
为新的"三范式对比 benchmark"补测试。同一批任务分别用 `loop`/`plan`/`graph` 跑，量化 成功率/轮数/tokens/耗时/改动文件，出对比报告（markdown + JSON）——把"三范式可对比"从口号变数字表。

## 现状：核心已由 Claude 完成（commit `1e5bd73`）
- `src/xhx_agent/evals/benchmark.py`：
  - `BenchmarkResult` 加字段 `mode`/`files_changed`/`repair_attempts`（都有默认值，向后兼容）。
  - `BenchmarkRunner._run_fixture(app, fixture, profile, mode)`：跑一个 (fixture, mode)，异常折成 failed（基准不崩）。
  - `BenchmarkRunner.run_benchmark(profile)`：**原行为不变**（单 profile、默认编排，3 个 fixture）。
  - `BenchmarkRunner.run_matrix(profile, modes=None)`：范式矩阵，`modes` 默认 `DEFAULT_BENCHMARK_MODES=["loop","plan","graph"]`，返回 `len(fixtures)*len(modes)` 个结果。
  - `render_benchmark_report(profile, results) -> BenchmarkReport`：按范式聚合（runs/success_rate/mean_turns/mean_tokens/mean_duration/total_files_changed）+ markdown 表。
  - CLI `xhx benchmark --modes loop,plan,graph`：跑矩阵 + 写 `.xhx/benchmark/report.md|report.json`（`--json` 则打印 JSON）。
- 现状 **334 passed, 1 skipped, ruff 绿**；Claude 内联冒烟过：mock 下 3×3=9 个结果、报告含 3 范式聚合、`run_benchmark` 向后兼容仍 3 个全 success。
- **已知（非 bug）**：mock 下 `graph` 成功率 0%——因为 mock 不是真 reviewer（永不回 PASS）；真实 profile 跑才有意义。

## 先读
- `src/xhx_agent/evals/benchmark.py`（核心）。
- `tests/test_evals.py::test_benchmark_runner`（现有 benchmark 测试，**必须仍绿**，是 run_benchmark 的契约）。

## 边界（不许动）
- `run_benchmark(profile)` 的原行为/签名不变（`test_benchmark_runner` 必须仍过）。
- 不改其他模块、不改编排器。
- 真实模型联调由协调者做，不在你范围。

## 关键 check 点（每个要有测试；建议加到 `tests/test_evals.py` 或新建 `tests/test_benchmark_matrix.py`）
1. **矩阵规模**：`run_matrix("mock", ["loop","plan"])` 返回 `3*2=6` 个结果；每个结果的 `mode` 在传入集合内；默认 `run_matrix("mock")` 用 `["loop","plan","graph"]` → 9 个。（用 mock profile + `init_project` 的 tmp workspace，参考 `test_benchmark_runner` 的环境搭法。）
2. **聚合正确**：`render_benchmark_report("mock", results)` 的 `summary` 含每个 mode；每个 mode 的 `runs` 等于该 mode 结果数；`success_rate` ∈ [0,1] 且 = 成功数/总数（可用构造好的假 `BenchmarkResult` 列表直接喂 `render_benchmark_report` 精确断言数值，**不必真跑**）。
3. **markdown 含范式**：报告 `markdown` 里出现每个 mode 名、含"按范式聚合"表头。
4. **向后兼容**：`run_benchmark("mock")` 仍返回 3 个、全 `success`（即 `test_benchmark_runner` 不破）。
5. **CLI `--modes`**：用 typer 的 `CliRunner`（参考现有 CLI 测试写法，若有）或直接调命令函数，`benchmark --modes loop,plan --json` 能产出含两范式的结构；非 json 时 `.xhx/benchmark/report.md` 被写出。（CLI 测试若环境麻烦，可降级为直接测 `render_benchmark_report` + `run_matrix`，但至少覆盖 check 点 1-4。）
6. **全绿**：`PYTHONUTF8=1 uv run pytest -q`（≥334 + 新测试）、ruff 绿。

> 提示：check 点 2 用**手工构造的 `BenchmarkResult` 列表**喂 `render_benchmark_report` 来精确断言聚合数值，最稳、不依赖真跑。check 点 1 才需要 `run_matrix` 真跑 mock。

## 纪律 / 明确排除
- TDD；命令前置 `PYTHONUTF8=1`；每步零回归 + ruff 绿；只在分支提交，不 push main，commit 只 add 测试文件（核心已提交）。
- **不做**：新增 benchmark fixture（尤其编辑型任务）、真实模型跑、报告可视化图表——留后续。
