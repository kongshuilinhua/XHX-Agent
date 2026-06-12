# Phase 7c — Textual 全屏富视图打磨（细状态行 + 流式）

> 把 7a 给 REPL 加的**细状态行 + 流式光标**也带给全屏 `xhx tui`。低风险、纯增量（保留所有 widget id），统一两套 UI 体验。
>
> **工作流**：UI 核心 Claude 已写好并视觉验证；**测试交 Gemini**。

## 已完成（Claude，本分支提交，勿改）
- `tui/textual_app.py`：
  - `TextualSnapshot` 新增 `status_line` 字段（`state · mode · turn · tokens · streaming`），`from_state` 填充。
  - 对话区：`state.is_streaming` 时显示 `model (streaming…)> …▌`（光标 + 标记），否则 `model> …`。
  - App `compose` 在 Header 下新增 `Static(id="statusline")`；`refresh_snapshot` 更新它；CSS 给 `#statusline`（height 1、`$accent` 粗体、`$panel` 背景）。
- **视觉验证**（Textual `export_screenshot()` 导 SVG + 快照字段确认）：状态行 `state: planning • mode: loop • turn: 2 • tokens: 42 • streaming: yes`；对话 `model (streaming…)> …▌`。全量 381 passed 零回归。

## 边界（不许动）
- 不改 `textual_app.py` 的逻辑/CSS。
- 现有 35 个 TUI 测试 + 全量 381 passed 必须保持（新增 widget 是 additive，已验证不破）。

## Gemini 要做：测试（并入 `tests/test_tui_textual.py`）
1. **status_line 字段**：`TextualSnapshot.from_state(state, …)` 的 `status_line` 含 `state.status`、`mode`、`turn`、`tokens`(=model_delta_count)、`streaming` 几个关键字段；`is_streaming=True` 时含 `streaming: yes`，否则 `streaming: no`。
2. **流式对话**：`is_streaming=True` 且有 `model_output` 时 `snapshot.conversation` 含 `model (streaming…)>` 与光标 `▌`；`is_streaming=False` 时是 `model>`、不含 `▌`。
3. **#statusline widget**：用 `app.run_test()` 挂载 `TextualCommandConsoleApp`，`query_one("#statusline", Static)` 存在；`refresh_snapshot` 后不抛。
4. **零回归**：`PYTHONUTF8=1 uv run pytest -q` 全绿（381→相近，含新增）+ `ruff check .` 全绿。

## 纪律
命令前置 `PYTHONUTF8=1`；只改 tests/（textual_app.py 核心已由 Claude 写好，勿动）；只在分支 `phase7c-textual-fullscreen-polish` 提交；全绿后 `git push origin phase7c-textual-fullscreen-polish`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
