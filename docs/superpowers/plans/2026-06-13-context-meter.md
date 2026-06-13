# 上下文精细计量（Block ③）实现计划

> **For agentic workers:** 逐 Task 实现，TDD，频繁提交。**硬约束：所有展示数值/文字必须来自真实 `ConsoleState` 字段，禁止 hardcode/mock/拿估算冒充精确。** 步骤用 `- [ ]`。

**Goal:** 让控制台精细展示上下文/token 占用——状态条 `Context 8.8k/128k 6.9%`（人性化+变色），`/context` 面板带进度条与分项明细。

**Architecture:** 纯 TUI 渲染 + 一个小状态字段。上下文/token 事件（`context_pack`/`token_usage`/`compaction`）已在 emit 并进 `ConsoleState`，本块只补 compaction 跟踪 + 渲染。**不动编排器/模型层。**

**Tech Stack:** Python 3.12 + Textual + Rich；pytest。

---

## 硬约束：真实值映射表（每个 UI 字段都必须指到真实来源）

| UI 显示 | 真实来源（ConsoleState 字段） | 事件 | 备注 |
|---|---|---|---|
| Context used / budget | `context_used_tokens_estimate` / `context_budget_tokens` | `context_pack` | used 是**估算**，如实标注，不冒充精确 |
| 占比 % + 进度条 | 由 used/budget 计算 | — | 仅当 `budget>0` 才显示，否则显示 `Context —` |
| 选中 / 省略文件 | `context_selected` / `context_omitted` | `context_pack` | |
| 本轮 turn | `context_turn` | `context_pack`/工具事件 | |
| 最近调用 prompt / completion | `tokens_prompt` / `tokens_completion` | `token_usage` | 最近一次调用（reduce 已存最近值） |
| 累计 token | `tokens_total` | `token_usage` | cumulative |
| 压缩次数 / 最近 before→after | `compaction_count` / `compaction_last_before` / `compaction_last_after`（**新增**） | `compaction` | 仅当 `compaction_count>0` 才显示该行 |

**规则**：拿不到真实值就优雅省略（不显示），不要凑数；零值/未开始时不画假进度条。

---

### Task 1: 纯格式化函数 `tui/format.py`

**Files:** Create `src/xhx_agent/tui/format.py`；Test `tests/test_tui_format.py`

**接口：**
- `def human_tokens(n: int) -> str`：`n<1000 → str(n)`；否则 `n/1000` 保留至多 1 位小数、去掉尾随 `.0`。例：`999→"999"`、`1000→"1k"`、`8800→"8.8k"`、`17835→"17.8k"`、`128000→"128k"`。
- `def context_meter(used: int, budget: int) -> tuple[str, float | None, str]`：返回 `(label, pct, level)`。
  - `budget<=0 → ("Context —", None, "none")`。
  - 否则 `pct=used/budget*100`；`label=f"Context {human_tokens(used)}/{human_tokens(budget)} {pct:.1f}%"`；`level= "ok" if pct<70 else "warn" if pct<90 else "crit"`。

- [ ] Step 1 写失败测试：`human_tokens` 上述 5 例；`context_meter(8800,128000)→ label 含 "8.8k/128k"、pct≈6.9、level=="ok"`；`context_meter(120000,128000)→ level=="crit"`；`context_meter(0,0)→ ("Context —", None, "none")`。
- [ ] Step 2 跑：`python -m pytest tests/test_tui_format.py -q` → FAIL
- [ ] Step 3 实现
- [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): pure context meter formatters (human_tokens/context_meter)`

---

### Task 2: `state.py` 跟踪 compaction（真实来源）

**Files:** Modify `src/xhx_agent/tui/state.py`；Test `tests/test_tui_state.py`

**行为：** `ConsoleState` 新增 `compaction_count: int = 0`、`compaction_last_before: int = 0`、`compaction_last_after: int = 0`。`reduce` 增 `elif event.type == "compaction":` 分支：`compaction_count += 1`，记录 `before`/`after`。

- [ ] Step 1 写失败测试：reduce 两个 `compaction` 事件（payload `before/after`）后，`compaction_count==2`、`compaction_last_before/after` 等于最后一个事件值。
- [ ] Step 2 跑 → FAIL
- [ ] Step 3 实现
- [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): track compaction count/last in ConsoleState`

---

### Task 3: 状态条接精细计量（真实字段）

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`TextualSnapshot.from_state` 状态条，约 111–117 行）

**行为：** 把状态条里 `ctx: {used}/{budget}` 段替换为 `context_meter(state.context_used_tokens_estimate, state.context_budget_tokens)` 的 `label`，并按 `level` 上色（`ok`→绿 `warn`→琥珀 `crit`→红；`none`→默认色）。`tokens:` 段用 `human_tokens(state.tokens_total)`。用 Textual/Rich 标记上色（状态条是 Static，支持 Rich markup）。**不得出现任何写死数字。**

- [ ] Step 1 实现（Gemini 编码）
- [ ] Step 2 全量测试不回归：`python -m pytest -q`（既有 `test_textual_snapshot_status_line` 可能需按新文案微调断言——同步更新为读真实字段后的输出）
- [ ] Step 3 commit：`feat(tui): status line shows human context meter with level color`

---

### Task 4: `/context` 面板分项明细（真实字段 + 优雅省略）

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`/context` 处理，约 1091 行 `handle_context`/对应方法）

**行为：** `/context` 的 detail 文本改为多行明细，全部读真实 state 字段（按映射表）：
```
Context {human_tokens(used)} / {human_tokens(budget)} ({pct}%)
{进度条：按 level 上色，budget<=0 时不画}
── 本轮 (turn {context_turn})
   选中文件 {context_selected} · 省略 {context_omitted} · 预算 {human_tokens(budget)}
── token
   最近调用 prompt {human_tokens(tokens_prompt)} · completion {human_tokens(tokens_completion)} · 累计 {human_tokens(tokens_total)}
── 压缩 (microcompact)        # 仅当 compaction_count>0 才输出本段
   已压缩 {compaction_count} 次 (最近 {compaction_last_before}→{compaction_last_after} 条)
```
- 进度条用方块字符按 `level` 上色；`budget<=0` 时只显示 `Context —`，不画条、不显示百分比。
- `compaction_count==0` 时**不显示**压缩段（不要写"已压缩 0 次"凑数）。
- 顶部 append 一行简短摘要（用 `context_meter` 的 label），保持与状态条一致。

- [ ] Step 1 实现
- [ ] Step 2 全量测试不回归：`python -m pytest -q`（更新 `test_textual_context_command_*` 断言为新明细输出，仍断言读自真实字段）
- [ ] Step 3 commit：`feat(tui): rich /context panel with real per-turn/token/compaction breakdown`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿。
- [ ] `python -m ruff check src/xhx_agent/tui/format.py src/xhx_agent/tui/state.py src/xhx_agent/tui/textual_app.py` 无**新增**违规。
- [ ] **真实值核对（关键）**：DeepSeek profile 跑一轮触发工具+多轮的任务，核对屏幕上的 used/budget/%、选中/省略、prompt/completion/累计、压缩次数与 `.xhx/traces/*` 里的 `context_pack`/`token_usage`/`compaction` 事件**一致**；故意跑到压缩触发，确认压缩段只在发生后出现且数字真实。

## Self-Review
- 真实值：四个任务每个 UI 字段都映射到真实 state 字段（见映射表），compaction 段补了真实来源（Task 2）。
- 占位符：无。
- 命名一致：`human_tokens`/`context_meter`/`compaction_count`/`compaction_last_before`/`compaction_last_after` 跨任务统一。
