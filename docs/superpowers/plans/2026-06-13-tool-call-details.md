# 工具调用细节展示（Block ①）实现计划

> **For agentic workers:** 逐 Task、TDD、频繁提交。**硬约束：展示的工具名/入参/状态/结果必须是真实运行数据（来自真实事件 → `ConsoleState`），禁 hardcode/mock。** 步骤用 `- [ ]`。

**Goal:** 时间线每次工具调用显示「工具 + 主参数」与「真实状态 + 结果」两行；`/tools` 面板看最近 N 次的完整入参/输出；`/verbose` 开关可全程内联完整细节。

**Architecture:** 借鉴 pi（per-tool renderer + 默认兜底 + showJsonMode）与 claude（`userFacingName(input)` 取主参数 + verbose）。把「带 status/summary/arguments 的 `tool_result` 事件」统一在 `_toolturn.py` 发（拿得到真实 `exec_result.status`），删掉 plan/loop 的光秃版——单一来源、真状态。**会动事件/编排层（已约定）。**

**Tech Stack:** Python 3.12 + Textual + Rich；pytest。

参考实现：`D:/pycharmprojects/pi/packages/web-ui/src/tools/`（renderer 注册表）、`D:/pycharmprojects/Claude-Code-main/src/components/messages/AssistantToolUseMessage.tsx`（userFacingName/verbose）。

**已知缺陷（本块顺带修）：** 当前 `tool_result` 事件只由 plan/loop 发且**不带 status**，导致**失败工具也渲染成 ✓**、结果行恒为 "Tool execution completed."。

---

## 真实值映射表（硬约束）

| UI 显示 | 真实来源 | 事件 |
|---|---|---|
| 工具名 + 主参数 | `tc.name` + `tc.arguments` | `tool_start`（新增 `arguments`） |
| 结果状态 ✓/✗ | `exec_result.status`（真实） | `tool_result`（新增 `status`） |
| 结果摘要 | `exec_result.summary`（真实） | `tool_result`（新增 `summary`） |
| /tools 完整入参 | `ToolActivity.arguments` | `tool_start` |

---

### Task 1: 事件采集——`tool_start` 带 args，`tool_result` 统一在 _toolturn 发（真状态）

**Files:** Modify `src/xhx_agent/orchestrators/_toolturn.py`、`src/xhx_agent/orchestrators/plan.py`、`src/xhx_agent/orchestrators/loop.py`；Test `tests/test_orchestrators.py`（无则就近新建）

**行为：**
- `_toolturn.py` `tool_start` 发射（约 52 行）加 `arguments=tc.arguments`。
- 重构 `_execute_tool_call_rich`：在函数末尾**统一发一条** `tool_result`，带真实 `status` 与 `summary`：
  - 用局部 `status`/`summary` 在各分支赋值：dispatch ok→`("success", 首行(content))`、dispatch 异常→`("error", str(exc))`；command→`(exec_result.status, exec_result.summary)`、异常→`("error", str(exc))`；structured denied→`("denied", policy.reason)`、ok→`(exec_result.status, exec_result.summary)`、异常→`("error", str(exc))`。
  - 末尾 `emit_event(ctx.event_callback, "tool_result", "Tool finished.", turn=turn, tool=tc.name, arguments=tc.arguments, status=status, summary=summary)`。（用 try/finally 或先算结果再 emit 再 return，避免多处重复。）
- **删除** plan.py（约 339 行）与 loop.py（约 156 行）里那条光秃的 `emit_event(... "tool_result", "Tool execution completed.", turn, tool=tc.name)`——改由 _toolturn 单一来源发。

- [ ] Step 1 写失败测试：用最小 ctx + 一个假 client/kernel 触发一次 search 工具，断言收到的事件里 `tool_start.payload["arguments"]` 含 query、`tool_result.payload` 含真实 `status` 与非空 `summary`；触发一次失败命令工具，断言 `tool_result.payload["status"]=="failed"/"error"`（而非 success）。
- [ ] Step 2 跑 → FAIL
- [ ] Step 3 实现
- [ ] Step 4 跑 → PASS；全量 `python -m pytest -q` 不回归（既有依赖 tool_result 的测试按新 payload 更新）。
- [ ] Step 5 commit：`feat(orchestrators): tool_start carries args; single rich tool_result with real status`

---

### Task 2: 纯函数 `tui/tool_display.py` —— per-tool header

**Files:** Create `src/xhx_agent/tui/tool_display.py`；Test `tests/test_tool_display.py`

**接口：** `def tool_header(tool: str, arguments: dict) -> str` —— 取每个工具的主参数生成一行：
- `search` / `repo_query` → `{tool} "{query}"`
- `read_file` → `read_file {path}`
- `terminal` → `$ {command}`；`verify` → `verify {command or '(default)'}`
- `apply_patch` → `apply_patch {file}`（best-effort：从 patch 文本正则提 `*** (Update|Add|Delete) File: (.+)` 或 `+++ b/(.+)`，提不到则 `apply_patch`）
- `dispatch` → `dispatch[{agent_type}] {description}`
- 兜底 → `{tool} {紧凑 json(arguments)}`（截断到 ~80 字）
所有取值用 `arguments.get(...)`，缺失优雅降级；整行截断到合理宽度。

- [ ] Step 1 写失败测试：上述每条一个用例（含兜底与缺参降级）。
- [ ] Step 2 跑 → FAIL  → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): per-tool header formatter (tool_display)`

---

### Task 3: `ConsoleState` 工具条目带 arguments + 真状态

**Files:** Modify `src/xhx_agent/tui/state.py`；Test `tests/test_tui_state.py`

**行为：** `ToolActivity` 加 `arguments: dict = field(default_factory=dict)`（已有 `tool/turn/status/summary`）。`tool_start` 处理存 `arguments=payload.get("arguments", {})`。`_update_tool`（tool_result）确保把 `status`、`summary` 从 payload 写进对应条目（状态用真实 payload 值）。

- [ ] Step 1 写失败测试：reduce 一个带 `arguments` 的 tool_start + 一个带 `status="failed"`/`summary` 的 tool_result → 对应 `ToolActivity.arguments`、`.status=="failed"`、`.summary` 正确。
- [ ] Step 2 跑 → FAIL → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): ToolActivity carries arguments; real status/summary on result`

---

### Task 4: 时间线渲染用 header + 真实状态

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`_timeline_line_for_event`）

**行为：**
- `tool_start` → `  ⟶ {tool_header(payload['tool'], payload.get('arguments', {}))}`（导入 `tool_header`）。
- `tool_result` → `  {✓ / ✗} {tool} → {summary}`，glyph 用真实 `payload['status']`（`failed/error/denied`→✗，否则✓）。
- `verbose` 开启时（见 Task 5）：tool_start 后补一行 `     args: {完整 json}`，结果行显示未截断 summary。

- [ ] Step 1 实现（更新 `test_textual_timeline_translates_runtime_events_into_messages`：tool_start 喂 arguments→断言行内出现主参数；tool_result 喂 `status="failed"`→断言出现 ✗）。
- [ ] Step 2 `python -m pytest -q` 不回归
- [ ] Step 3 commit：`feat(tui): timeline shows tool header (primary arg) + real status glyph`

---

### Task 5: `/tools` 面板 + `/verbose` 开关

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（新增 `handle_tools`、`verbose` 字段、命令分发、帮助/补全）

**行为：**
- `self.verbose: bool = False`；`/verbose` 切换并 `append_message(f"system> verbose: {'on' if ... else 'off'}")`。
- `/tools`：`set_detail("tools", ...)` 列最近 N（如 10）个 `state.tools`，每条：
  ```
  {✓/✗} {tool_header(t.tool, t.arguments)} → {t.summary}
       入参 {紧凑 json(t.arguments)}        # 完整入参（截断到面板宽度即可）
  ```
  失败条目用红色（Rich markup）。无工具→`system> 本次会话还没有工具调用`。
- 命令分发加 `/tools`、`/verbose`；帮助文本与补全候选（命令清单各处）补这两条，保持一致。

- [ ] Step 1 实现 + TUI 测试：构造若干 tool_start/tool_result 事件喂 state 后，`/tools` 的 `app.messages`/detail 含主参数与入参；`/verbose` 切换后 `app.verbose` 翻转且时间线出现 `args:` 行。
- [ ] Step 2 `python -m pytest -q` 不回归
- [ ] Step 3 commit：`feat(tui): /tools detail panel + /verbose inline full args/output`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿；`ruff check` 改动文件无新增违规。
- [ ] **真实值核对（关键）**：DeepSeek 跑一轮含 search/read_file/apply_patch/verify 的任务——
  - 时间线每个工具显示真实主参数（路径/命令/query）；
  - **失败的 verify 显示 ✗（不再是 ✓）**、结果摘要真实；
  - `/tools` 的入参与 `.xhx/traces/*` 的 `tool_result` trace 一致；`/verbose` 开后内联完整入参/输出。

## Self-Review
- 真实值：状态/摘要取自 `exec_result`（真实），arguments 取自 `tc.arguments`（真实）；映射表逐项可指。
- 顺带修了「失败工具显示 ✓」的真实性缺陷。
- 命名一致：`tool_header`/`ToolActivity.arguments`/`verbose`/`handle_tools`。
- 折叠连续 read/search：本块不做（YAGNI，用户已确认）。
