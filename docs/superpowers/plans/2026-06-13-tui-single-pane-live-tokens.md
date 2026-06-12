# TUI 单栏时间线 + 实时 token Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Textual 全屏控制台合并成单栏时间线（工具/子 agent/验证按时序进主区），顶部状态条接 provider 精确 usage 实时刷新 token，覆盖 loop/plan/graph 三种模式；不改 agent 行为。

**Architecture:** token 在最低层（模型客户端）捕获——`ChatResult` 新增 `usage`，`chat_and_count` 把真实 usage 累加并 emit `token_usage` 事件；TUI 的 `ConsoleState.reduce` 接住它写进状态字段，状态条渲染。可见性通过 `apply_runtime_event` 把运行时事件就地翻译成一行 append 进**单一有序消息流** `self.messages`（避免独立 timeline 拼接打乱时序）。右栏 widget 删除、对话区拉满宽。

**Tech Stack:** Python 3、pydantic、httpx（MockTransport 测试）、Textual（`run_test` pilot）、pytest。

**对应 spec:** `docs/superpowers/specs/2026-06-12-tui-single-pane-live-tokens-design.md`

**与 spec 的一处有意偏差:** spec 3.3 列了 `policy_decision → ⚠ 权限` 时间线行。实现中**不**为 `policy_decision` 单独加时间线行——权限可见性已由现有权限 picker + `permission required/allowed/declined` 消息覆盖，再加一行会重复，且会干扰若干断言 `messages[-1]/[-2]` 的测试。其余事件按 spec 翻译。

---

## File Structure

| 文件 | 责任 |
|---|---|
| `src/xhx_agent/models/types.py` | 新增 `TokenUsage`；`ChatResult.usage` |
| `src/xhx_agent/models/openai_compatible.py` | 非流式/流式从响应抓 `usage` 塞进 `ChatResult` |
| `src/xhx_agent/orchestrators/_toolturn.py` | `chat_and_count` 用真实 usage 累加并 emit `token_usage` |
| `src/xhx_agent/tui/state.py` | token 字段 + `token_usage` reduce |
| `src/xhx_agent/tui/textual_app.py` | 状态条接真实 token/ctx/verify/changed；事件→消息翻译；删 `#side` |
| `tests/test_openai_compatible.py` | usage 解析测试 |
| `tests/test_toolturn.py` | `chat_and_count` emit 测试 |
| `tests/test_tui_textual.py` | token reduce、时间线、状态条、删右栏后的测试迁移 |

---

## Task 1: TokenUsage 类型 + ChatResult.usage

**Files:**
- Modify: `src/xhx_agent/models/types.py`
- Test: `tests/test_chat_client.py`（追加一个用例）

- [ ] **Step 1: 写失败测试**

在 `tests/test_chat_client.py` 末尾追加：

```python
def test_chat_result_carries_optional_token_usage() -> None:
    from xhx_agent.models.types import ChatResult, TokenUsage

    empty = ChatResult()
    assert empty.usage is None

    used = ChatResult(usage=TokenUsage(prompt=10, completion=4, total=14))
    assert used.usage is not None
    assert used.usage.prompt == 10
    assert used.usage.completion == 4
    assert used.usage.total == 14
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_chat_client.py::test_chat_result_carries_optional_token_usage -v`
Expected: FAIL（`ImportError: cannot import name 'TokenUsage'`）

- [ ] **Step 3: 最小实现**

在 `src/xhx_agent/models/types.py` 的 `ChatResult` 之前加 `TokenUsage`，并给 `ChatResult` 加 `usage` 字段：

```python
class TokenUsage(BaseModel):
    prompt: int = 0
    completion: int = 0
    total: int = 0


class ChatResult(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage | None = None
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_chat_client.py::test_chat_result_carries_optional_token_usage -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/xhx_agent/models/types.py tests/test_chat_client.py
git commit -m "feat(models): add TokenUsage and ChatResult.usage"
```

---

## Task 2: 模型客户端捕获 provider usage（非流式 + 流式）

**Files:**
- Modify: `src/xhx_agent/models/openai_compatible.py`
- Test: `tests/test_openai_compatible.py`

- [ ] **Step 1: 写失败测试（非流式 + 流式各一个）**

在 `tests/test_openai_compatible.py` 末尾追加：

```python
def test_chat_captures_usage_nonstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert result.usage is not None
    assert result.usage.prompt == 11
    assert result.usage.completion == 5
    assert result.usage.total == 16


def test_chat_stream_captures_usage_and_requests_include_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    captured_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured_body.update(_json.loads(request.read().decode("utf-8")))
        body = (
            'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        stream=True,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.set_delta_callback(lambda _text: None)

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert captured_body.get("stream") is True
    assert captured_body.get("stream_options") == {"include_usage": True}
    assert result.content == "hello"
    assert result.usage is not None
    assert result.usage.total == 9
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_openai_compatible.py::test_chat_captures_usage_nonstream tests/test_openai_compatible.py::test_chat_stream_captures_usage_and_requests_include_usage -v`
Expected: FAIL（`result.usage is None`；`stream_options` 缺失）

- [ ] **Step 3: 实现 usage 捕获**

在 `src/xhx_agent/models/openai_compatible.py`：

(a) 顶部 import 加 `TokenUsage`：

```python
from xhx_agent.models.types import ChatResult, ModelClientError, ModelPlan, TokenUsage, ToolCall
```

(b) 新增解析 helper（放在 `_message_to_chat_result` 附近）：

```python
def _parse_usage(raw: Any) -> TokenUsage | None:
    if not isinstance(raw, dict):
        return None
    return TokenUsage(
        prompt=int(raw.get("prompt_tokens", 0) or 0),
        completion=int(raw.get("completion_tokens", 0) or 0),
        total=int(raw.get("total_tokens", 0) or 0),
    )
```

(c) `_chat_nonstream`：把 `usage` 一并塞进结果。替换 `try/except` 后的解析与 return：

```python
        try:
            data = response.json()
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelClientError(
                code="invalid_response",
                message="Chat response missing choices[0].message.",
                details={"body": response.text[:1000]},
            ) from exc
        result = _message_to_chat_result(message)
        return result.model_copy(update={"usage": _parse_usage(data.get("usage"))})
```

(d) `_chat_stream`：payload 加 `stream_options`，循环里捕获 usage，组装时传入。替换方法体内相应部分：

```python
        stream_payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        content_parts: list[str] = []
        tool_frags: dict[int, dict[str, str]] = {}
        usage_raw: Any = None
        try:
            with self.http_client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=stream_payload,
            ) as response:
                if response.status_code >= 400:
                    raise ModelClientError(
                        code="http_error",
                        message=f"Chat request returned HTTP {response.status_code}.",
                        details={
                            "status_code": response.status_code,
                            "body": response.read().decode("utf-8", errors="replace")[:1000],
                        },
                    )
                for raw_line in response.iter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    line = raw_line.removeprefix("data:").strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("usage"):
                        usage_raw = data["usage"]
                    self._consume_stream_delta(data, content_parts, tool_frags)
        except ModelClientError:
            raise
        except httpx.HTTPError as exc:
            raise ModelClientError(
                code="network_error", message=f"Chat request failed: {exc}", details={"error": str(exc)}
            ) from exc
        return _assemble_stream_chat(content_parts, tool_frags, usage_raw)
```

(e) `_assemble_stream_chat`：加可选 usage 参数并设置到结果。替换签名与最后两行：

```python
def _assemble_stream_chat(
    content_parts: list[str], tool_frags: dict[int, dict[str, str]], usage_raw: Any = None
) -> ChatResult:
    """把流式累积的 content 片段与按 index 拼接的 tool_call 片段组装成最终 ChatResult。"""
    tool_calls: list[ToolCall] = []
    for index in sorted(tool_frags):
        slot = tool_frags[index]
        if not slot["name"]:
            continue
        raw = slot["args"]
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                code="invalid_tool_arguments",
                message=f"streamed tool_call arguments not valid JSON: {raw[:200]}",
                details={"arguments": raw[:1000]},
            ) from exc
        tool_calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=args))
    content = "".join(content_parts)
    return ChatResult(content=content or None, tool_calls=tool_calls, usage=_parse_usage(usage_raw))
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_openai_compatible.py -v`
Expected: PASS（含新增两个用例，旧用例不回归）

- [ ] **Step 5: 提交**

```bash
git add src/xhx_agent/models/openai_compatible.py tests/test_openai_compatible.py
git commit -m "feat(models): capture provider token usage in chat() (stream + non-stream)"
```

---

## Task 3: chat_and_count 用真实 usage 累加并 emit token_usage

**Files:**
- Modify: `src/xhx_agent/orchestrators/_toolturn.py:25-31`
- Test: `tests/test_toolturn.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_toolturn.py` 末尾追加（用轻量 stub，不碰真实 kernel）：

```python
def test_chat_and_count_emits_real_token_usage():
    from types import SimpleNamespace
    from xhx_agent.models.types import ChatResult, TokenUsage
    from xhx_agent.orchestrators._toolturn import chat_and_count

    events = []
    ctx = SimpleNamespace(metrics_tracker={"tokens": 0}, event_callback=lambda e: events.append(e))

    class FakeClient:
        def chat(self, messages, schemas):
            return ChatResult(content="ok", usage=TokenUsage(prompt=10, completion=6, total=16))

    result = chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "hi"}], [])

    assert result.content == "ok"
    token_events = [e for e in events if e.type == "token_usage"]
    assert len(token_events) == 1
    assert token_events[0].payload["total"] == 16
    assert token_events[0].payload["cumulative_total"] == 16
    assert ctx.metrics_tracker["tokens_real"] == 16

    # 第二次调用应累加 cumulative_total
    chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "again"}], [])
    token_events = [e for e in events if e.type == "token_usage"]
    assert token_events[-1].payload["cumulative_total"] == 32


def test_chat_and_count_no_usage_emits_no_token_event():
    from types import SimpleNamespace
    from xhx_agent.models.types import ChatResult
    from xhx_agent.orchestrators._toolturn import chat_and_count

    events = []
    ctx = SimpleNamespace(metrics_tracker={"tokens": 0}, event_callback=lambda e: events.append(e))

    class FakeClient:
        def chat(self, messages, schemas):
            return ChatResult(content="ok")  # usage None（provider 未返回）

    chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "hi"}], [])

    assert [e for e in events if e.type == "token_usage"] == []
    # 估算路径仍然累加，保证回退可用
    assert ctx.metrics_tracker["tokens"] > 0
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_toolturn.py::test_chat_and_count_emits_real_token_usage tests/test_toolturn.py::test_chat_and_count_no_usage_emits_no_token_event -v`
Expected: FAIL（无 `token_usage` 事件、无 `tokens_real`）

- [ ] **Step 3: 实现**

替换 `src/xhx_agent/orchestrators/_toolturn.py` 中的 `chat_and_count`：

```python
def chat_and_count(ctx: OrchestratorContext, client: Any, messages: list[dict], schemas: list[dict]) -> Any:
    """调 client.chat，累加 token 指标并在拿到 provider usage 时 emit token_usage 事件。

    - 估算路径（_estimate_message_tokens）保持不变：run_end 的 tokens_estimate 与回退仍可用。
    - 若 ChatResult 带 provider usage，则把真实 total 累加进 metrics_tracker['tokens_real']，
      并 emit 'token_usage'（cumulative_total 供状态条实时显示）。
    """
    ctx.metrics_tracker["tokens"] = ctx.metrics_tracker.get("tokens", 0) + _estimate_message_tokens(messages)
    result = client.chat(messages, schemas)
    usage = getattr(result, "usage", None)
    if usage is not None:
        cumulative = ctx.metrics_tracker.get("tokens_real", 0) + int(usage.total or 0)
        ctx.metrics_tracker["tokens_real"] = cumulative
        emit_event(
            ctx.event_callback,
            "token_usage",
            "Token usage updated.",
            prompt=int(usage.prompt or 0),
            completion=int(usage.completion or 0),
            total=int(usage.total or 0),
            cumulative_total=cumulative,
        )
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_toolturn.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/xhx_agent/orchestrators/_toolturn.py tests/test_toolturn.py
git commit -m "feat(orchestrators): emit token_usage event from real provider usage"
```

---

## Task 4: ConsoleState 加 token 字段 + token_usage reduce

**Files:**
- Modify: `src/xhx_agent/tui/state.py`
- Test: `tests/test_tui_textual.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_tui_textual.py` 末尾追加：

```python
def test_state_reduce_token_usage_tracks_cumulative_total() -> None:
    from xhx_agent.tui.state import ConsoleState
    from xhx_agent.runtime.events import RuntimeEvent

    state = ConsoleState()
    assert state.tokens_total == 0

    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 10, "completion": 6, "total": 16, "cumulative_total": 16},
        )
    )
    assert state.tokens_prompt == 10
    assert state.tokens_completion == 6
    assert state.tokens_total == 16

    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 8, "completion": 8, "total": 16, "cumulative_total": 32},
        )
    )
    assert state.tokens_total == 32
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tui_textual.py::test_state_reduce_token_usage_tracks_cumulative_total -v`
Expected: FAIL（`AttributeError: ... 'tokens_total'`）

- [ ] **Step 3: 实现**

在 `src/xhx_agent/tui/state.py` 的 `ConsoleState` 里，`model_delta_count` 之后加字段：

```python
    model_delta_count: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
```

在 `reduce()` 里加一个分支（放在 `elif event.type == "model_delta":` 同级链上即可，建议紧邻 model_delta 之后）：

```python
        elif event.type == "token_usage":
            self.tokens_prompt = int(payload.get("prompt", self.tokens_prompt) or 0)
            self.tokens_completion = int(payload.get("completion", self.tokens_completion) or 0)
            self.tokens_total = int(payload.get("cumulative_total", self.tokens_total) or 0)
```

注意：`token_usage` 不在第 80-91 行的 `is_streaming` 开关集合里，保持流式状态不被它打断（它在模型调用结束后到达）。

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tui_textual.py::test_state_reduce_token_usage_tracks_cumulative_total -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/xhx_agent/tui/state.py tests/test_tui_textual.py
git commit -m "feat(tui): track cumulative provider token usage in ConsoleState"
```

---

## Task 5: 状态条显示真实 token + ctx/verify/changed

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py:84-87`（`TextualSnapshot.from_state` 的 `status_line`）
- Test: `tests/test_tui_textual.py`（迁移 `test_textual_snapshot_status_line`）

- [ ] **Step 1: 改测试为真实 token 口径（先让它失败）**

把现有 `test_textual_snapshot_status_line` 整体替换为：

```python
def test_textual_snapshot_status_line() -> None:
    state = ConsoleState()
    state.status = "running"
    state.mode = "loop"
    state.context_turn = 3
    state.context_used_tokens_estimate = 400
    state.context_budget_tokens = 6000
    state.verification = "running"
    state.changed_files = ["a.py", "b.py"]
    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 100, "completion": 20, "total": 120, "cumulative_total": 120},
        )
    )
    state.is_streaming = True  # type: ignore[attr-defined]

    snapshot = TextualSnapshot.from_state(
        state, workspace="/repo", profile="mock", auto_repair=False, assume_yes=True
    )
    assert "state: running" in snapshot.status_line
    assert "mode: loop" in snapshot.status_line
    assert "turn: 3" in snapshot.status_line
    assert "tokens: 120" in snapshot.status_line
    assert "ctx: 400/6000" in snapshot.status_line
    assert "verify: running" in snapshot.status_line
    assert "changed: 2" in snapshot.status_line
    assert "streaming: yes" in snapshot.status_line

    state.is_streaming = False  # type: ignore[attr-defined]
    snapshot_non_streaming = TextualSnapshot.from_state(
        state, workspace="/repo", profile="mock", auto_repair=False, assume_yes=True
    )
    assert "streaming: no" in snapshot_non_streaming.status_line
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tui_textual.py::test_textual_snapshot_status_line -v`
Expected: FAIL（`tokens: 120` 找不到——当前显示 `model_delta_count`=0；无 `ctx:`/`verify:`/`changed:`）

- [ ] **Step 3: 实现 status_line**

把 `TextualSnapshot.from_state` 里的 `status_line`（约 84-87 行）替换为：

```python
        status_line = (
            f"state: {state.status}  •  mode: {state.mode}  •  turn: {state.context_turn or 0}"
            f"  •  tokens: {state.tokens_total}"
            f"  •  ctx: {state.context_used_tokens_estimate}/{state.context_budget_tokens or 0}"
            f"  •  verify: {state.verification}  •  changed: {len(state.changed_files)}"
            f"  •  streaming: {'yes' if streaming else 'no'}"
        )
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tui_textual.py::test_textual_snapshot_status_line -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/xhx_agent/tui/textual_app.py tests/test_tui_textual.py
git commit -m "feat(tui): status line shows real tokens, ctx, verify, changed count"
```

---

## Task 6: 事件→消息时间线翻译（单一有序流）

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（`apply_runtime_event` 约 1092-1094 行；新增 helper）
- Test: `tests/test_tui_textual.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_tui_textual.py` 末尾追加：

```python
def test_textual_timeline_translates_runtime_events_into_messages(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    app.handle_runtime_event(
        RuntimeEvent(type="tool_start", message="", payload={"tool": "search", "turn": 1})
    )
    app.handle_runtime_event(
        RuntimeEvent(
            type="tool_result",
            message="",
            payload={"tool": "search", "status": "success", "summary": "0 hits", "turn": 1},
        )
    )
    app.handle_runtime_event(
        RuntimeEvent(type="graph_review", message="round 1: passed", payload={"round": 1})
    )
    app.handle_runtime_event(
        RuntimeEvent(type="verification_start", message="", payload={"command": "python -m pytest"})
    )
    app.handle_runtime_event(
        RuntimeEvent(
            type="verification_result",
            message="",
            payload={"command": "python -m pytest", "status": "failed", "exit_code": 1},
        )
    )

    joined = "\n".join(app.messages)
    assert "⟶ tool  search" in joined
    assert "✓ tool  search → 0 hits" in joined
    assert "▸ agent  review  round 1: passed" in joined
    assert "⚙ verify  python -m pytest" in joined
    assert "→ failed(exit 1)" in joined


def test_textual_timeline_skips_non_visible_events(tmp_path) -> None:
    """run_start/run_end/cancel_requested 等已有专门处理或不该进时间线，避免打乱 messages 索引。"""
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    before = len(app.messages)
    app.handle_runtime_event(
        RuntimeEvent(type="run_start", message="", payload={"run_id": "r1", "task": "t", "profile": "mock"})
    )
    app.handle_runtime_event(RuntimeEvent(type="model_delta", message="hello", payload={"turn": 1}))
    assert len(app.messages) == before
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tui_textual.py::test_textual_timeline_translates_runtime_events_into_messages -v`
Expected: FAIL（messages 里没有 `⟶ tool` 等行）

- [ ] **Step 3: 实现 helper + 接入 apply_runtime_event**

在 `TextualCommandConsoleApp` 里新增方法（放在 `apply_runtime_event` 上方）：

```python
    def _timeline_line_for_event(self, event: RuntimeEvent) -> str | None:
        """把运行时事件翻译成一行时间线文本；不可见事件返回 None。

        只翻译当前不产生消息行的事件，避免与已有 append 重复或打乱 messages 索引。
        policy_decision 故意不在此（权限可见性已由 picker + permission 消息覆盖）。
        """
        et = event.type
        p = event.payload or {}
        if et == "tool_start":
            return f"  ⟶ tool  {p.get('tool', '?')}"
        if et == "tool_result":
            summary = (p.get("summary") or event.message or "").strip().replace("\n", " ")
            if len(summary) > 80:
                summary = summary[:80] + "…"
            glyph = "✗" if str(p.get("status")) in {"failed", "error"} else "✓"
            tail = f" → {summary}" if summary else ""
            return f"  {glyph} tool  {p.get('tool', '?')}{tail}"
        if et in {"graph_coordinator", "graph_worker", "graph_execute", "graph_review"}:
            role = et.removeprefix("graph_")
            msg = (event.message or "").strip().replace("\n", " ")
            if len(msg) > 100:
                msg = msg[:100] + "…"
            return f"▸ agent  {role}  {msg}".rstrip()
        if et == "verification_start":
            return f"  ⚙ verify  {p.get('command', '')}"
        if et == "verification_result":
            code = p.get("exit_code")
            tail = f"(exit {code})" if code is not None else ""
            return f"  ⚙ verify  {p.get('command', '')} → {p.get('status', '')}{tail}"
        if et == "model_plan":
            return f"plan> {event.message}"
        return None
```

替换 `apply_runtime_event`：

```python
    def apply_runtime_event(self, event: RuntimeEvent) -> None:
        self.state.reduce(event)
        line = self._timeline_line_for_event(event)
        if line is not None:
            # 单一有序流：事件行与 user>/assistant>/system> 共用 self.messages，时序天然正确。
            self.messages.append(line)
        self.refresh_snapshot()
```

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tui_textual.py::test_textual_timeline_translates_runtime_events_into_messages tests/test_tui_textual.py::test_textual_timeline_skips_non_visible_events -v`
Expected: PASS

- [ ] **Step 5: 回归 + 提交**

Run: `python -m pytest tests/test_tui_textual.py -v`
Expected: 仅 Task 7 待迁移的 6 个 widget/旧用例可能失败；其余 PASS（尤其 steer/verify 的 `messages[-1]/[-2]` 断言不应回归——若回归，说明翻译集合多翻了事件，回查 helper）。

```bash
git add src/xhx_agent/tui/textual_app.py tests/test_tui_textual.py
git commit -m "feat(tui): translate tool/agent/verify events into the single message timeline"
```

---

## Task 7: 删右栏（单栏满宽）+ 迁移失效测试

**Files:**
- Modify: `src/xhx_agent/tui/textual_app.py`（CSS、`compose`、`refresh_snapshot`）
- Test: `tests/test_tui_textual.py`（迁移 5 个 widget-query 用例）

> 说明：`TextualSnapshot` 的 `runtime_state/changed_files/details/commands` 字段**保留为计算值**（仍被 `/diff`、`active_detail`/`detail_text` 等内部状态与 snapshot-级测试使用），仅删除右栏 **widget** 与其渲染。这样 snapshot 级测试不动，只迁移直接 query 右栏 widget 的用例。

- [ ] **Step 1: 先迁移测试（让它们成为新契约）**

(a) `test_textual_command_console_app_can_render_initial_shell` —— 删掉 `#changed`/`#details` 断言，改为：

```python
            assert "No conversation yet." in str(pilot.app.query_one("#conversation").content)
            assert "state: idle" in str(pilot.app.query_one("#statusline").content)
            assert len(pilot.app.query("#side")) == 0
```

(b) `test_textual_command_console_submitted_task_updates_window` —— 把 `#runtime` 那行改为状态条：

```python
            assert "summary> .xhx/logbook/run-1.md" in str(pilot.app.query_one("#conversation").content)
            assert "verify: skipped_no_changes" in str(pilot.app.query_one("#statusline").content)
```

(c) `test_textual_fullscreen_runs_real_runtime_python_fixture_with_permission` —— 最后一行 `#runtime` 改为：

```python
            assert "verify: passed" in str(pilot.app.query_one("#statusline").content)
```

(d) `test_textual_fullscreen_permission_can_wait_for_allow` —— 把 `#runtime` 的 `waiting:` 断言替换为：

```python
            assert app.pending_confirmation is not None
            assert app.pending_confirmation.command == "python -m pytest"
```

(e) `test_textual_app_input_focus_retention` —— 不再有 `#side`，改用对话区触发 blur：

```python
            # Try to blur input by focusing another widget
            other = pilot.app.query_one("#conversation")
            other.focus()
            await pilot.pause()

            # Focus should be forced back to input
            assert input_widget.has_focus
```

- [ ] **Step 2: 运行确认失败**

Run: `python -m pytest tests/test_tui_textual.py -k "initial_shell or submitted_task_updates or fullscreen_runs_real or wait_for_allow or input_focus_retention" -v`
Expected: FAIL（`#side`/`#statusline` 断言尚未成立——右栏还在、状态条还没 verify 字段时还好；主要是 `query("#side")==0` 失败、`#conversation` focus 行为）

- [ ] **Step 3: 删右栏 widget 与渲染**

在 `src/xhx_agent/tui/textual_app.py`：

(a) CSS：删掉 `#side { ... }` 整段；把 `#conversation_scroll` 的 `width: 2fr;` 改为 `width: 1fr;`：

```python
    #conversation_scroll {
        width: 1fr;
        border: solid $primary;
    }
```

(b) `compose()`：把 `with Horizontal(id="body"):` 块内的 `#side` 整段删掉，只留对话列：

```python
        with Horizontal(id="body"):
            with VerticalScroll(id="conversation_scroll"):
                yield Static(id="conversation")
                yield Vertical(id="interactive_container")
```

(c) `refresh_snapshot()`：删掉对已移除 widget 的 `update` 调用，仅保留状态条与对话区：

```python
        try:
            self.query_one("#statusline", Static).update(snapshot.status_line)
            self.query_one("#conversation", Static).update(snapshot.conversation)
        except Exception:
            pass
```

（即删除 `#runtime`/`#changed`/`#details`/`#commands` 四行 update。）

- [ ] **Step 4: 运行确认通过**

Run: `python -m pytest tests/test_tui_textual.py -v`
Expected: PASS（全绿）

- [ ] **Step 5: 全局排查残留引用**

Run: `python -m pytest tests/ -q`
并检索是否还有别处引用被删 widget：

Run（grep，应只剩 snapshot 字段、不该再有 widget query）：
`rg -n "#side|#runtime\"|#changed\"|#details\"|#commands\"" tests src`
Expected: `tests` 中无 `query_one("#side"/"#runtime"/...)` 残留；`src` 中 `textual_app.py` 不再 `query_one` 这些 id。

- [ ] **Step 6: 提交**

```bash
git add src/xhx_agent/tui/textual_app.py tests/test_tui_textual.py
git commit -m "feat(tui): merge side panel into single-pane timeline"
```

---

## Task 8: 全量校验 + 真实联通验收

**Files:** 无（验收）

- [ ] **Step 1: 全量单测**

Run: `python -m pytest tests/ -q`
Expected: 全绿。若 `test_cli.py` / `test_console_answer.py` 等触及状态条/快照文案，按同样口径（`verify:`/`tokens:` 等）迁移其断言后再绿。

- [ ] **Step 2: 真实联通肉眼验收（DeepSeek profile，控制台 UTF-8）**

按记忆 `real-llm-testing` 的方式启动全屏控制台，跑一个简单任务，确认：
1. 无右栏，主区单栏满宽；
2. loop 与 graph 模式都能在主区看到 `⟶ tool / ✓ tool / ▸ agent / ⚙ verify` 逐条出现；
3. 顶部 `tokens:` 随每轮真实跳动（非 0、非 delta 计数）。

- [ ] **Step 3: 收尾提交（如有文案/断言微调）**

```bash
git add -A
git commit -m "test(tui): align remaining assertions with single-pane + real tokens"
```

---

## Self-Review 记录

- **Spec 覆盖**：3.1 布局→Task7；3.2 信息去向→Task5(状态条)+Task6(时间线)+保留 snapshot 字段；3.3 事件映射→Task6（policy_decision 有意省略，已在顶部说明）；3.4 token 管线→Task1-4；3.5 状态条格式→Task5。全部有对应任务。
- **占位扫描**：无 TBD/TODO；每个改动步骤都给了真实代码或确切断言。
- **类型/命名一致**：`TokenUsage(prompt/completion/total)`、`ChatResult.usage`、事件 `token_usage` 的 payload 键 `prompt/completion/total/cumulative_total`、state 字段 `tokens_prompt/tokens_completion/tokens_total`、`metrics_tracker['tokens_real']` 在 Task1→3→4→5 中一致。
</content>
