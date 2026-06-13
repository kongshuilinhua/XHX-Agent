# 对话配色（Block ②a）实现计划

> **For agentic workers:** 逐 Task、TDD、频繁提交。步骤用 `- [ ]`。

**Goal:** 把 `#conversation` 时间线按角色/类型分色，user/assistant/system/工具/plan 一眼可辨，提升可读性。

**Architecture:** 纯渲染。颜色**不写进 `self.messages`**（保持 view-log 纯文本），只在更新 `#conversation` Static 时用 Rich `Text` 逐行按 `line_style(line)` 上色——字面追加、不解析 markup（顺带消除内容里 `[` 触发 markup 解析的隐患）。**不动模型/编排层。**

**Tech Stack:** Python 3.12 + Textual/Rich；pytest。

配色方案（已与用户确认的 mockup）：user→青(cyan) · assistant→亮(bright_white) · system→黄(yellow) · 工具调用 `⟶`→蓝(blue) · 成功 `✓`→绿(green) · 失败 `✗`→红(red) · verify `⚙`→蓝 · graph `▸ agent`→品红(magenta) · plan→蓝 · summary→暗(dim) · `model (streaming…)`/`💭`思考→暗斜(dim italic) · 其它→默认。

---

### Task 1: 纯函数 `line_style(line)`

**Files:** Modify `src/xhx_agent/tui/format.py`；Test `tests/test_tui_format.py`

**接口：** `def line_style(line: str) -> str` —— 返回 Rich style 字符串（空串=默认）。判定用前缀与（去左空格后的）首字符：
```
prefix 判定（startswith）：
  "user>" → "cyan"
  "assistant>" → "bright_white"
  "system>" → "yellow"
  "plan>" → "blue"
  "summary>" → "dim"
  "model (streaming" → "dim italic"
去左空格后首字形（line.lstrip() startswith）：
  "⟶" → "blue"      "✓" → "green"     "✗" → "red"
  "⚙" → "blue"      "▸ agent" → "magenta"   "💭" → "dim italic"
其它 → ""（默认）
```

- [ ] Step 1 写失败测试：每个分支一个用例（含缩进的 `  ✓ tool ...`→green、`  ✗ ...`→red、`  ⟶ ...`→blue、普通文本→""）。
- [ ] Step 2 跑 → FAIL  → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): line_style — per-line conversation color mapping`

---

### Task 2: `refresh_snapshot` 用 Rich Text 上色渲染

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`refresh_snapshot`，约 407–435 行）

**行为：** 把更新 `#conversation` 的那行从 `update(snapshot.conversation)` 改为构造 Rich `Text` 后 `update(text)`：
```python
from rich.text import Text
from xhx_agent.tui.format import line_style
...
conv_text = snapshot.conversation
text = Text()
for line in conv_text.split("\n"):
    text.append(line + "\n", style=line_style(line))
self.query_one("#conversation", Static).update(text)
```
- 滚动长度判定继续用 `conv_text`（str）的 `len`，逻辑不变。
- `snapshot.conversation` 仍是 str（不改 `TextualSnapshot` 契约，既有断言不受影响）。

- [ ] Step 1 实现。
- [ ] Step 2 全量 `python -m pytest -q` 不回归；补一个 pilot 测试：跑一条带 `user>`/`system>` 的对话后，`query_one("#conversation").renderable` 是 `Text` 且其 `.plain` 含原文（证明文字未丢、渲染不崩）。
- [ ] Step 3 commit：`feat(tui): colorize conversation timeline by role/type via Rich Text`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿；`ruff check` 两文件无新增违规。
- [ ] 真机：对话里 user(青)/assistant(亮)/system(黄)/工具调用(蓝)/成功(绿)/失败(红) 一眼可分；含 `[` 的工具入参行不再因 markup 解析出错。

## Self-Review
- 颜色只在渲染期施加，view-log 仍纯文本（与会话恢复兼容）。
- 命名一致：`line_style`。改动面最小（format.py + refresh_snapshot 一处）。
