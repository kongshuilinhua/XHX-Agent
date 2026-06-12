# Phase 3b-2 — 默认路由切到 tool-calling（loop）

> 省略 `--mode` 时，默认从 legacy ModelPlan(linear/dag 自动分类) 改为统一的 **tool-calling `loop`**——
> 让默认路径与三范式一致。**ModelPlan/linear/dag/planner/dry-run 全部保留**（仅不再是默认），不做大规模删除（纯删除高风险、零能力增益）。
>
> **工作流**：核心（app.py 默认切换）Claude 已做并验证；**受影响测试的手术交 Gemini**（规则明确、清单完整）。

## 已完成（Claude，本分支提交，勿改）
- `runtime/app.py`：run_task 里删掉 `ModeClassifier().classify(...)`，`ctx.mode = mode or "loop"`，`orchestrator = select_orchestrator(mode)`（None→loop，经 `DEFAULT_MODE`）；移除 `ModeClassifier` / `execution_mode_to_key` 两个不再用的 import。ruff 干净。
- 已验证原则：给 legacy 测试加 `mode="linear"` 即转绿（见下两例，已改）。
  - `tests/test_runtime.py::test_run_task_writes_report`、`test_run_task_emits_runtime_events` → 已加 `mode="linear"`，通过。

## 规则（照此做手术）
默认切到 loop 后，**断言 legacy ModelPlan 行为的测试**（`model_plan`/`context_pack` 事件、`verification=="skipped_no_changes"`、`plan_summary`、auto-repair、mock 闭环、确认回路…）会失败——因为它们测的是**仍然保留**的 linear/dag 路径。**修法 = 把它们钉到当年自动分类会落到的显式 mode**（绝大多数是 `mode="linear"`；明确测 DAG 的用 `mode="dag"`），让它们继续测这条保留的路径。**不要改这些测试的断言逻辑，只补 mode 参数。**

## Gemini 要做

### A) `tests/test_runtime.py` — 给这些 run_task 调用补 `mode="linear"`
（其它已通过的 run_task 不要动；只改下列失败用例的那一处 run_task）
- `test_python_fixture_mock_closed_loop`、`test_node_fixture_mock_closed_loop`、`test_runtime_failed_verification_stops_and_reports`：`run_task("fix failing test", assume_yes=True)` → 加 `mode="linear"`（这三处字符串相同）。
- `test_runtime_refreshes_repo_index_after_patch_before_verification`：`run_task("add public api", assume_yes=False, event_callback=events.append)` → 加 `mode="linear"`。
- `test_runtime_requires_confirmation_without_yes`：`run_task("fix failing test")` → 加 `mode="linear"`。
- `test_runtime_confirmation_callback_executes_verification`：那条 run_task → 加 `mode="linear"`。
- `test_runtime_auto_repair_attempts_second_patch`、`test_runtime_auto_repair_stops_at_attempt_limit`：`run_task("fix demo", profile_name="real", assume_yes=True, auto_repair=True)` → 加 `mode="linear"`（两处相同）。
- `test_runtime_rejects_invalid_model_plan_before_tool_execution`：`run_task("bad plan")` → 加 `mode="linear"`。
- `test_runtime_feeds_tool_results_into_next_model_turn`：`run_task("analyze README", profile_name="real")` → 加 `mode="linear"`。
- `test_runtime_emits_model_delta_events_for_streaming_profiles`：`run_task("analyze", profile_name="real", event_callback=events.append)` → 加 `mode="linear"`。
- `test_runtime_stops_when_real_model_exceeds_max_turns`：`run_task("analyze forever", profile_name="real")` → 加 `mode="linear"`。

### B) `tests/test_planner.py::test_runtime_app_routes_by_mode`
该测当年测的是「自动分类→linear/dag」，默认改 loop 后前提作废。把三个 case 钉到显式 mode：case 1（"what is Python?"）、case 2（"analyze this repo"）→ `mode="linear"`；case 3（"refactor math"，断言 changed_files/turns==1）→ `mode="dag"`。断言不变。

### C) `tests/test_skills.py::test_runtime_app_hooks_integration`
其 run_task → 加 `mode="linear"`（hooks 在 ModelPlan 的 apply_patch 上触发）。

### D) `tests/test_command_console.py::test_command_console_runs_task_and_keeps_last_result`
在 `handle_input("analyze this repo")` **之前**加 `command_console.mode = "linear"`（这样 `orchestrator_mode` → "linear"，走 legacy 路径，断言的 plan_summary/skipped_no_changes 才成立）。

### E) `tests/test_tui_textual.py::test_textual_fullscreen_runs_real_runtime_python_fixture_with_permission`
让该全屏测试以 linear 模式跑（读 `TextualCommandConsoleApp` 看它怎么暴露 mode——多半也有 `.mode` 或经 console；设成 `"linear"`，使 ModelPlan 路径跑到 verify 终端命令、触发 permission 确认）。

### F) 新增：断言新默认 = loop
加一条 `tests/` 用例：mock profile 下 `run_task("analyze this repo")`（**不传 mode**）→ `result.mode == "loop"`（且不报错）。证明默认已是 tool-calling。

## Checkpoints
1. A–E 的失败用例全部转绿，且**断言逻辑未被削弱**（只补了 mode 参数 / 设置 console.mode / app mode）。
2. F：无 mode 的 run_task → `result.mode == "loop"`。
3. `PYTHONUTF8=1 uv run pytest -q` 全绿（380→相近，含新增）；`ruff check .` 全绿；零非预期回归。

## 纪律
命令前置 `PYTHONUTF8=1`；只在分支 `phase3b2-default-toolcalling` 提交；只改测试文件（app.py 核心已由 Claude 改好，勿动）；全绿后 `git push origin phase3b2-default-toolcalling`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
