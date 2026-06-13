# 思考过程展示（Block ②d）实现计划

> **For agentic workers:** 逐 Task、TDD、频繁提交。**硬约束：思考内容必须是模型真实返回的 reasoning_content，禁 hardcode/mock；模型不返回就不显示（优雅省略）。** 步骤用 `- [ ]`。

**Goal:** 把支持 reasoning 的模型（如 deepseek-reasoner）返回的思维链，渲染成暗斜的 `💭 思考 ({model}) …` 块（默认折叠为首段，`/verbose` 展开更长）。

**Architecture:** 模型层抓 `reasoning_content`（非流式读 message、流式累积 delta）→ `ChatResult.reasoning` → `chat_and_count` emit `model_thinking` 事件 → 时间线渲染 💭 块。`line_style` 的 `💭`→dim italic 已在 ②a 就位。**不改编排流程；普通模型无 reasoning 时全链路自动跳过。**

**Tech Stack:** Python 3.12 + httpx + Textual；pytest。

诚实前提：仅 reasoning 能力模型返回 `reasoning_content`；deepseek-chat/v4-flash 不返回 → 不显示思考块（符合"拿不到真实值就省略"）。

---

### Task 1: 模型层抓 reasoning（`ChatResult.reasoning` + 非流式/流式）

**Files:** Modify `src/xhx_agent/models/types.py`、`src/xhx_agent/models/openai_compatible.py`；Test `tests/test_openai_compatible.py`（无则就近）

**行为：**
- `types.py`：`ChatResult` 加 `reasoning: str | None = None`。
- `openai_compatible.py`：
  - `_message_to_chat_result`（约 458 行 return）：加 `reasoning=message.get("reasoning_content")`（非 str 则 None）。
  - 流式：`_chat_stream` 加 `reasoning_parts: list[str] = []`，传入 `_consume_stream_delta`；后者 `r = delta.get("reasoning_content"); if isinstance(r, str) and r: reasoning_parts.append(r)`；`_assemble_stream_chat` 末尾 `reasoning="".join(reasoning_parts) or None` 写进返回的 ChatResult。
  - 注意：reasoning 累积**不要**喂给现有 `delta_callback`（那是 content 流），保持 content 行为零变更。

- [ ] Step 1 写失败测试：
  - 非流式：mock 一个 `message` 带 `reasoning_content="想一想…"` → `_message_to_chat_result(...).reasoning=="想一想…"`；无该字段 → `reasoning is None`。
  - 流式：喂含 `delta.reasoning_content` 分片的 SSE 序列 → 组装出的 ChatResult.reasoning 拼接正确；content 与 tool_calls 行为不变。
- [ ] Step 2 跑 → FAIL → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS（全量不回归）
- [ ] Step 5 commit：`feat(models): capture reasoning_content into ChatResult (stream + nonstream)`

---

### Task 2: `chat_and_count` 上报 `model_thinking`

**Files:** Modify `src/xhx_agent/orchestrators/_toolturn.py`（`chat_and_count`）；Test `tests/test_toolturn.py`

**行为：** `client.chat` 返回后，若 `getattr(result, "reasoning", None)` 非空，emit：
```python
reasoning = getattr(result, "reasoning", None)
if reasoning:
    emit_event(ctx.event_callback, "model_thinking", "Model reasoning.",
               turn=turn, model=getattr(client, "model", ""), text=reasoning)
```
放在 token_usage emit 附近（turn 已是 ②c 加的参数）。

- [ ] Step 1 写失败测试：假 client 返回带 `reasoning="思考X"` 的 result → 捕获到 `model_thinking` 事件，payload `text=="思考X"`、`turn`/`model` 正确；reasoning 为 None → 不发该事件。
- [ ] Step 2 跑 → FAIL → [ ] Step 3 实现 → [ ] Step 4 跑 → PASS
- [ ] Step 5 commit：`feat(orchestrators): emit model_thinking when model returns reasoning`

---

### Task 3: 时间线渲染 💭 思考块（折叠/verbose）

**Files:** Modify `src/xhx_agent/tui/textual_app.py`（`_timeline_line_for_event`）

**行为：** 新增 `if et == "model_thinking":` 分支：
```python
text = (p.get("text") or "").strip()
if not text:
    return None
model = p.get("model", "")
oneline = " ".join(text.split())
limit = 500 if getattr(self, "verbose", False) else 60
if len(oneline) > limit:
    oneline = oneline[:limit] + "…"
return f"  💭 思考 ({model}) {oneline}"
```
- `line_style` 的 `💭`→`dim italic` 已在 ②a 就位，无需改。

- [ ] Step 1 实现 + 测试：喂 `model_thinking`(text 多行/model) → `app.messages` 出现 `💭 思考 (model)` 且内容单行截断；`verbose` 开后同事件展示更长；text 空 → 不新增行。
- [ ] Step 2 全量 `python -m pytest -q` 不回归
- [ ] Step 3 commit：`feat(tui): render collapsible thinking block from model_thinking`

---

## 收尾验证（Claude 验收）
- [ ] `python -m pytest -q` 全绿；`ruff check` 改动文件无新增违规。
- [ ] **真实值核对（关键）**：用 **deepseek-reasoner**（或其它 reasoning 模型）profile 跑一轮——时间线出现暗斜 `💭 思考 (模型) …`，内容是模型真实思维链（与 provider 返回的 reasoning_content 一致）、`/verbose` 展开更长；换 deepseek-chat（无 reasoning）跑 → **不出现**思考块（优雅省略）。

## Self-Review
- 真实值：思考内容直接取自 `message/delta.reasoning_content`，无 hardcode；模型不返回则全链路跳过。
- content/tool_calls/usage 行为零变更（reasoning 独立累积，不进 content delta_callback）。
- 命名一致：`ChatResult.reasoning` / `model_thinking` payload `text/model/turn`。
- 真·交互折叠展开：本块用"截断/verbose"近似，后续需要再做。
