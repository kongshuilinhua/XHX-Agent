"""async_wrapper.py 单测：同步 chat 客户端 → 异步流事件。"""

from __future__ import annotations

import asyncio
from typing import Any

from xhx_agent.models.async_wrapper import AsyncClientWrapper, wrap_sync_client
from xhx_agent.tools.base import StreamEnd, TextDelta, ToolCallComplete


class _DictClient:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result
        self.last_messages: list[dict[str, Any]] | None = None

    def chat(self, messages: list[dict[str, Any]], tools: Any = None) -> dict[str, Any]:
        self.last_messages = messages
        return self._result


class _BoomClient:
    def chat(self, messages: Any, tools: Any = None) -> Any:
        raise RuntimeError("boom")


def _drain(wrapper: AsyncClientWrapper, **kwargs: Any) -> list[Any]:
    async def _run() -> list[Any]:
        return [ev async for ev in wrapper.stream([{"role": "user", "content": "hi"}], **kwargs)]

    return asyncio.run(_run())


def test_text_and_usage_dict() -> None:
    client = _DictClient({"content": "你好", "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
    events = _drain(wrap_sync_client(client))
    assert any(isinstance(e, TextDelta) and e.text == "你好" for e in events)
    end = [e for e in events if isinstance(e, StreamEnd)][0]
    assert end.input_tokens == 10 and end.output_tokens == 3
    assert end.stop_reason == "end_turn"


def test_tool_calls_dict_form() -> None:
    client = _DictClient(
        {
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "Write", "arguments": '{"path": "x.py"}'}}],
        }
    )
    events = _drain(AsyncClientWrapper(client))
    tc = [e for e in events if isinstance(e, ToolCallComplete)][0]
    assert tc.tool_name == "Write"
    assert tc.arguments == {"path": "x.py"}  # JSON 字符串被解析为 dict
    end = [e for e in events if isinstance(e, StreamEnd)][0]
    assert end.stop_reason == "tool_use"


def test_error_yields_stream_end() -> None:
    events = _drain(AsyncClientWrapper(_BoomClient()))
    assert len(events) == 1
    assert isinstance(events[0], StreamEnd)
    assert events[0].stop_reason == "error"


def test_system_prompt_prepended() -> None:
    client = _DictClient({"content": "ok"})
    _drain(AsyncClientWrapper(client), system="你是助手")
    assert client.last_messages is not None
    assert client.last_messages[0] == {"role": "system", "content": "你是助手"}


def test_object_tool_call_with_name_attr() -> None:
    class _TC:
        id = "c2"
        name = "search"
        arguments = {"q": "x"}

    class _ObjResult:
        content = ""
        tool_calls = [_TC()]
        usage = None

    class _ObjClient:
        def chat(self, messages: Any, tools: Any = None) -> Any:
            return _ObjResult()

    events = _drain(AsyncClientWrapper(_ObjClient()))
    tc = [e for e in events if isinstance(e, ToolCallComplete)][0]
    assert tc.tool_name == "search" and tc.arguments == {"q": "x"}
