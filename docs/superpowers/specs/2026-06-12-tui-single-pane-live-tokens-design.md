# TUI 单栏时间线 + 实时 token 设计

- 日期：2026-06-12
- 范围：仅 Textual 全屏控制台的**可见性**改造，不改变任何 agent 决策行为
- 状态：已与用户确认，待转实现计划

## 1. 背景与问题

用户用 **graph 模式**跑了一个简单的网页贪吃蛇任务，跑了很久且 `verification: failed`，而界面上几乎看不到 agent 在做什么。根因有三：

1. **graph 模式是黑盒**：graph 编排器只发 `graph_coordinator / graph_worker / graph_execute / graph_review` 事件，而 `tui/state.py` 的 `reduce()` 不处理这些类型——它们只被堆进 `state.events`（`events: 133`），永远不进对话区或右栏。用户全程只能看到 `user>` → `summary>` → `run finished: failed`。
2. **`tokens: 0` 是接错了指标**：顶部状态条的 `tokens` 实际显示 `state.model_delta_count`（流式增量块计数），graph 不走流式所以恒为 0。真实 token 既没从 provider 抓（`ChatResult` 把 `usage` 丢了），也没在状态条体现。
3. **工具调用只显示"当前一个"**：loop 模式本来发 `tool_start/tool_result/model_delta`，但 UI 只把"正在运行的那一个工具"塞进右栏 `active tool`，历史工具调用、子 agent 步骤都没汇聚到主区。

## 2. 目标与非目标

**目标**
- 删除右栏，主区变满宽的单栏时间线，所有工具调用 / 子 agent 步骤 / 验证 / 权限按时间顺序内嵌显示。
- 让 graph / plan / loop 三种模式都把执行过程喂进主时间线（根治"黑盒")。
- 顶部细状态条接 **provider 精确 usage**，按轮实时刷新真实 token。

**非目标 / 范围边界**
- 不改 agent 的决策、编排、文件落点等行为（不解决"为什么 graph 选错路"——那是另一档工作）。
- 只改 Textual 全屏控制台（`tui/textual_app.py` + `tui/state.py`）。rich 版控制台（`tui/page.py` / `tui/live.py`）的单栏一致性**默认不做**，用户明确要才做。

## 3. 设计

### 3.1 布局：单栏时间线 + 顶部实时状态条

```
┌ xhx-agent | running | profile: deepseek | run: run-… ───────────────────────┐
│ state: running · mode: loop · turn: 2 · tokens: 3,418 · ctx: 3.4k/16k ·      │  ← 顶部状态条（实时）
│ verify: running · changed: 4 · streaming                                     │
├──────────────────────────────────────────────────────────────────────────────┤
│ user> 实现一个网页端贪吃蛇                                                     │
│ plan> 3 步：建页面 / 写逻辑 / 自测                                             │
│ model (streaming…)> 我先看一下目录结构…▌                                       │
│   ⟶ tool  search  "*.html"                                                    │
│   ✓ tool  search  → 0 命中                                                    │
│   ⟶ tool  apply_patch  game.html                                             │
│   ✓ tool  apply_patch  → +48 行                                              │
│ ▸ agent  coordinator  拆成 3 个子任务         （graph 模式才出现）            │
│ ▸ agent  review  第 1 轮：未通过（缺少自测）                                   │
│   ⚙ verify  python -m pytest  → failed (exit 1)                              │
│ assistant> 已生成 game.html，但自测未通过，原因：…                             │
│ summary> .xhx/logbook/run-….md                                              │
├──────────────────────────────────────────────────────────────────────────────┤
│ > 输入任务或 /命令                                                            │
└──────────────────────────────────────────────────────────────────────────────┘
```

- `compose()` 删除 `#side`（`#runtime/#changed/#details/#commands` 四个 Static），`#conversation_scroll` 宽度改为满宽（去掉 `width: 2fr`，`#side` 的 CSS 整段删除）。
- 顶部 `#statusline` 保留，内容增强（见 3.4）。

### 3.2 右栏信息去向（不丢信息）

| 原右栏内容 | 新位置 |
|---|---|
| `active tool` / `active verification` | 时间线 `⟶ tool` / `⚙ verify` 行（含历史，非仅当前） |
| mode / verification / context / changed 数量 | 顶部状态条，实时刷新 |
| `events` 计数、workspace、flags、pending steer | 不再常驻，可走 `/status`、`/dashboard` 按需查 |
| changed files 完整清单 | 顶部只显数量；清单走已有的 `/diff` |
| `details:`（/plan /context… 明细） | 这些命令本就同时 `append_message` 打了一条进对话区，右栏 details 冗余，删除 |
| slash 命令提示列表 | 收进 `/help` 与输入自动补全（已有），不常驻 |

### 3.3 事件 → 时间线映射（核心：让所有模式可见）

在 `state.reduce()` 里把以下事件翻译成一行时间线（覆盖所有模式）：

| 事件 | 渲染 |
|---|---|
| `tool_start` | `  ⟶ tool  <tool>` |
| `tool_result` | `  ✓ tool  <tool> → <summary 截断>`（失败用 `✗`） |
| `graph_coordinator/worker/execute/review` | `▸ agent  <角色>  <message 截断>` |
| `verification_start` | `  ⚙ verify  <command>` |
| `verification_result` | `  ⚙ verify  <command> → <status>(exit <code>)` |
| `policy_decision`（requires_user 或 confirm/deny） | `  ⚠ 权限：<source/scope> (<risk>)` |
| `model_plan` | `plan> <summary>`（已有） |
| `model_delta` | 流式光标（已有） |

**实现建议（关键，避免时序 bug）**：不要新建独立的 `timeline` 列表再与 `messages` 拼接——那会把工具行排到 `assistant>` 回答之后，破坏时间顺序。改为**单一有序消息流**：

- 事件 → 文本行的翻译放在 `textual_app.apply_runtime_event` 里（它在 UI 线程、按事件到达顺序执行，且 `messages` 归它所有）：先 `state.reduce(event)`，再把该事件翻译成一行 `self._append_message(...)` 进同一个 `self.messages`。
- 这样用户动作产生的行（`user>`/`system>`/`assistant>`/`summary>`）与事件产生的行（`⟶ tool`/`▸ agent`/`⚙ verify`/`plan>`）共用一个按时间排序的列表，时序天然正确。
- 翻译只针对当前**不产生**消息行的事件（`tool_start/tool_result/graph_*/verification_*/policy_decision/model_plan`），不会与已有 append 重复。`model_delta` 仍走现有流式光标预览，不进 `messages`。

### 3.4 token = provider 精确 usage

当前 `client.chat()` 返回 `ChatResult(content, tool_calls)`，**丢弃了 `usage`**。管线补齐：

```
chat() 拿到响应
  ├─ 非流式（_chat_nonstream）：response.json()["usage"] → {prompt_tokens, completion_tokens, total_tokens}
  └─ 流式（_chat_stream）：payload 加 stream_options.include_usage=true；
        末尾会多一条 choices 为空、带 usage 的 chunk → 在 _consume_stream_delta 捕获，
        _assemble_stream_chat 时塞进 ChatResult
        ↓
ChatResult 新增 usage 字段（TokenUsage: prompt/completion/total，可为 None）
        ↓
_toolturn.chat_and_count：拿到 result.usage 后，把真实 total 累加进
  ctx.metrics_tracker['tokens']（替换原字符估算累加），并 emit_event "token_usage"
  payload={prompt, completion, total, cumulative_total}
        ↓
state.reduce 处理 token_usage → 写入 tokens_prompt/completion/total/cumulative
        ↓
顶部状态条显示 cumulative_total（每个模型调用结束跳一次——符合用户选定口径）
```

- `models/types.py`：新增 `TokenUsage`（prompt/completion/total: int），`ChatResult` 加 `usage: TokenUsage | None = None`。
- usage 缺失（部分 provider 不回）时回退为 None，状态条显示上一次已知值或 `—`，不报错。
- `plan()` / `summarize()` 调用的 usage 暂不接（次要路径），只接 tool-calling 主路径 `chat()`。

### 3.5 状态条格式

`status_line` 两行（或一行紧凑），字段：
`state · mode · turn · tokens(cumulative_total，千分位) · ctx(used/budget，k 简写) · verify · changed(数量) · streaming`

不再使用 `model_delta_count` 作为 tokens；`model_delta_count` 仍可保留用于 `streaming` 判断或删除。

## 4. 改动清单（关键 check 点）

| 文件 | 改动 |
|---|---|
| `src/xhx_agent/models/types.py` | 新增 `TokenUsage`；`ChatResult.usage` |
| `src/xhx_agent/models/openai_compatible.py` | 非流式读 `usage`；流式加 `stream_options.include_usage` 并从末尾 chunk 取 usage；塞进 `ChatResult` |
| `src/xhx_agent/orchestrators/_toolturn.py` | `chat_and_count` 用真实 usage 累加 `metrics_tracker['tokens']` 并 emit `token_usage` |
| `src/xhx_agent/tui/state.py` | 加 token 字段（prompt/completion/total/cumulative）；`reduce` 处理 `token_usage` 事件 |
| `src/xhx_agent/tui/textual_app.py` | `compose` 删 `#side`、对话满宽；`apply_runtime_event` 把 tool/graph/verify/policy/model_plan 事件就地翻译成一行 append 进 `messages`（单一有序流）；`TextualSnapshot` 状态条接真实 token/ctx/verify/changed；CSS 删 `#side`；`refresh_snapshot` 不再更新被删 widget |
| `tests/test_tui_textual.py`（及相关快照测试） | 更新断言：右栏没了、状态条有真实 token、时间线含工具/agent/verify 行 |

## 5. 验证方式（验收 check 点）

- 单测：`pytest tests/test_tui_textual.py` 及触及的 state/snapshot 测试全绿。
- 真实联通（DeepSeek profile，控制台 UTF-8）跑一个简单任务，肉眼确认：
  1. 无右栏，主区单栏；
  2. loop 与 graph 模式都能在主区看到工具/子 agent/验证逐条出现；
  3. 顶部 `tokens` 随每轮真实跳动（非 0、非 delta 计数）。

## 6. 待定 / 显式排除

- rich 版控制台（`page.py`/`live.py`）单栏一致性：默认不做。
- agent 行为正确性（graph 选错路、文件散落、verification 失败原因展示）：本次不做，属另一档工作。
- `plan()`/`summarize()` 的 token usage：本次不接。
