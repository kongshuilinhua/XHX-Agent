# 每步指标（Block ②b）实现计划

> **For agentic workers:** 逐 Task、TDD、频繁提交。**硬约束：展示的 model/耗时/token 必须真实（client.model + perf_counter 实测 + provider usage），禁 hardcode/mock。** 步骤用 `- [ ]`。

**Goal:** 每次模型调用在时间线显示一行暗色指标 `· {model} · {秒}s · in {prompt}/out {completion}`；状态条带上当前 model 名。

**Architecture:** 只改唯一共享调用点 `chat_and_count`（7 处调用自动受益），给已有 `token_usage` 事件补 `model`+`duration_ms`；state 记最近一次 model/耗时；时间线/状态条渲染。**不动模型 provider 包本身、不动编排流程。** 这行指标是 ②c 步骤树 turn header 的素材。

**Tech Stack:** Python 3.12 + Textual/Rich；pytest。

---

### Task 1: `chat_and_count` 实测耗时 + 带 model（补进 token_usage）

**Files:** Modify `src/xhx_agent/orchestrators/_toolturn.py`（`chat_and_count`，约 25–47 行）；Test `tests/test_orchestrators.py`（就近）

**行为：** 用 `time.perf_counter()` 包住 `client.chat`；`model = getattr(client, "model", "")`；在 `usage is not None` 分支的 `emit_event("token_usage", ...)` 里**追加** `model=model, duration_ms=duration_ms`（保留现有 prompt/completion/total/cumulative_total 字段）。
```python
import time
...
t0 = time.perf_counter()
result = client.chat(messages, schemas)
duration_ms = int((time.perf_counter() - t0) * 1000)
usage = getattr(result, "usage", None)
if usage is not None:
    ...
    emit_event(ctx.event_callback, "token_usage", "Token usage updated.",
               prompt=..., completion=..., total=..., cumulative_total=cumulative,
               model=getattr(client, "model", ""), duration_ms=duration_ms)
```

- [ ] Step 1 写失败测试：假 client（`.model="deepseek-x"`、`.chat` 返回带 usage 的 result）+ 捕获事件的假 ctx → 断言 token_usage payload 含 `model=="deepseek-x"` 且 `duration_ms` 为 int≥0。
- [ ] Step 2 跑 → FAIL → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS（全量不回归）
- [ ] Step 5 commit：`feat(orchestrators): chat_and_count records real model + duration on token_usage`

---

### Task 2: `ConsoleState` 记最近一次 model + 耗时

**Files:** Modify `src/xhx_agent/tui/state.py`；Test `tests/test_tui_state.py`

**行为：** `ConsoleState` 加 `last_model: str = ""`、`last_call_ms: int = 0`。`token_usage` reduce 末尾写入 `self.last_model = str(payload.get("model", self.last_model) or "")`、`self.last_call_ms = int(payload.get("duration_ms", 0) or 0)`（既有 tokens 写入不变）。

- [ ] Step 1 写失败测试：reduce 一个带 `model`/`duration_ms` 的 token_usage → `last_model`/`last_call_ms` 正确；不带这些字段的旧事件不报错（向后兼容）。
- [ ] Step 2 跑 → FAIL → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(tui): track last_model/last_call_ms in ConsoleState`

---

### Task 3: 时间线指标行 + 状态条 model + 暗色

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`_timeline_line_for_event`、状态条 `from_state`）、`src/xhx_agent/tui/format.py`（`line_style`）

**行为：**
- `_timeline_line_for_event`：新增 `if et == "token_usage":` 分支——**仅当 payload 有非空 `model` 时**返回一行（否则 None，保持旧"不渲染"）：
  ```
  secs = p.get("duration_ms", 0) / 1000
  return f"  · {p['model']} · {secs:.1f}s · in {human_tokens(int(p.get('prompt',0)))}/out {human_tokens(int(p.get('completion',0)))}"
  ```
- 状态条 `from_state`：`tokens: ...` 后追加 ` · {state.last_model}`（仅当非空）。
- `line_style`：去左空格后 `startswith("·")` → 返回 `"dim"`。

- [ ] Step 1 实现 + 测试：
  - `line_style("  · deepseek · 2.4s ...")=="dim"`；
  - timeline 测试喂一个带 model/duration_ms/prompt/completion 的 token_usage → `app.messages` 出现 `· deepseek` 指标行；喂不带 model 的 token_usage → 不新增行（更新既有相关断言）；
  - 状态条测试：`state.last_model="deepseek-x"` → `snapshot.status_line` 含 `deepseek-x`。
- [ ] Step 2 全量 `python -m pytest -q` 不回归
- [ ] Step 3 commit：`feat(tui): per-call metrics line (model/duration/tokens) + model in status line`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿；`ruff check` 改动文件无新增违规。
- [ ] **真实值核对**：DeepSeek 跑一轮——每次模型调用出现暗色 `· {真实模型名} · {真实秒数}s · in/out`，数值与 `.xhx/traces/*` 的 token 一致、模型名与所用 profile 一致；状态条显示模型名。

## Self-Review
- 真实值：model=client.model、duration=perf_counter 实测、tokens=provider usage；映射可指。
- 只改一个共享点 `chat_and_count`，7 处调用自动覆盖；provider 包不动。
- 命名一致：`last_model`/`last_call_ms`/`duration_ms`。
- 与 ②c 衔接：本行指标即 turn header 素材，②c 再做轮次分组。
