# Phase 7a — 流式渲染 + 细状态行（渲染重做）

> 把 tool-calling 的 `chat()` 接成流式（已完成），并按 §10 五原则重做 REPL 仪表盘渲染：
> 追加式滚屏 + **细状态行** + 宽度自适应 + 渲染防抖。历史压缩 / repo-intel-as-tool 留 7b。
>
> **工作流**：流式**核心 Claude 已写好并真模型验证**；**渲染重做 + 渲染脚本 + 全部测试交 Gemini**。

## 已完成（Claude，本分支提交，勿改）
- `models/openai_compatible.py`：`chat()` 在 `stream=True` 且挂了 `delta_callback` 时走流式（`_chat_stream`）——实时把 content 增量喂回调，并按 `index` 拼装分片的 `tool_calls`；非流式路径不变（抽出 `_chat_nonstream`/`_message_to_chat_result`）。加 `set_delta_callback()`。
- `models/__init__.py`：`build_chat_client` 传 `stream=profile.stream`。
- `models/routing.py`：`FallbackChatClient.set_delta_callback` 转发给被包客户端。
- `orchestrators/loop.py`：建好 client 后 `set_delta_callback(lambda t: emit_event(ctx.event_callback, "model_delta", t))`。
- **真 DeepSeek 验证**：content 16 增量精确重组；纯工具调用时流式 `search` tool_call 正确组装。
- **零行为变更**：流式只在 `stream=True`+挂回调时触发；现有 362 passed 全绿。

> 渲染管线已存在：`model_delta` 事件 → `state.reduce`（累积 `model_output`/`model_delta_count`）→ `LiveDashboard.refresh()` 重渲 `render_console_page`。所以"流式可见"已基本通；本切片是**渲染重做 + 防抖**。

## 边界（不许动）
- 不改流式核心（上面四个文件的流式逻辑）。
- 不破坏现有 TUI/console 测试语义；零回归（362 passed 基线）。

## Gemini 要做

### 1) 渲染重做（`tui/page.py` / `tui/live.py` / `cli/console.py`，§10 五原则）
- **细状态行**：在仪表盘顶部加一条**单行**紧凑状态行，至少含 `state · mode · turn · tokens(用 state.model_delta_count 或 metrics) · 是否 streaming`，宽度自适应、过长省略不串列（参考现有 `_header_table` 已修过的 workspace 折行问题）。
- **流式区**：让 `model_output` 以"正在流式"的样式独立成区（如标题带 `▌` 光标或 `streaming…` 标记），与已完成消息区分。
- **防抖**：`model_delta` 现在每 token 强刷整屏（`console.handle_event`→`refresh_live_dashboard`）。改成**合并/防抖**——例如 `model_delta` 不强制 `refresh=True`、交给 Rich `Live` 的 `refresh_per_second` 定时刷，或在 console 侧把 model_delta 的刷新限频到 ≤10/s。其它事件仍即时刷。
- 保持"追加式滚屏不清屏"（Rich `Live(transient=False)` 已是）。

### 2) 渲染脚本（新 `scripts/render_dashboard.py`）
- 用代表性的 `ConsoleState`（含一个"streaming 进行中"状态 + 一个"完成"状态）调 `render_console_page`，用 `rich.console.Console(record=True).export_svg()` 导出到 `.xhx/render/*.svg`（或参数化输出路径）。供人工/我可视核对渲染效果。**不写进仓库根**。

### 3) 测试（snapshot/文本级，参考 `tests/test_tui_textual.py` 的 `TextualSnapshot` 与 `app.run_test()`）
覆盖下方 check 点。

## Checkpoints
1. **流式增量驱动渲染**：构造若干 `model_delta` 事件喂 `ConsoleState.reduce`，`render_console_page` 输出含累积的流式文本，且 `model_delta_count` 反映增量数。
2. **细状态行**：渲染输出（文本化）包含单行状态行且含 state/mode/turn/tokens 关键字段；workspace/长字段不串列（沿用既有折行）。
3. **防抖**：`model_delta` 事件不再每条强制整屏 `refresh=True`（用 monkeypatch 计数 `Live.update`/`refresh_live_dashboard` 调用，断言 model_delta 批量下刷新次数被限频/少于事件数）；非 model_delta 事件仍即时刷。
4. **渲染脚本**：`scripts/render_dashboard.py` 能跑通并产出非空 SVG（断言文件存在、含 `<svg`）。
5. **零行为变更 + 全绿**：现有 TUI/console 测试语义不破坏；`PYTHONUTF8=1 uv run pytest -q`（362→更多 passed）+ `ruff check .` 全绿。

## 纪律
TDD；命令前置 `PYTHONUTF8=1`；ruff B023 默认参数绑定循环变量；只在分支 `phase7a-streaming-render` 提交；全绿后 `git push origin phase7a-streaming-render`。报告：新增 commit `git log --oneline`、pytest 统计行、ruff 结果、每个 check 点一句话。
