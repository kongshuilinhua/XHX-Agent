"""client.py 单测：缓存标记/工具转换/MockClient/create_client/resolve_context_window。"""

from __future__ import annotations

import asyncio

from xhx_agent.client import (
    AuthenticationError,
    LLMError,
    MockClient,
    NetworkError,
    OpenAICompatClient,
    RateLimitError,
    _mark_last_tool_for_cache,
    _mark_last_user_tail_for_cache,
    _supports_adaptive_thinking,
    create_client,
    resolve_context_window,
)
from xhx_agent.config import ProviderConfig
from xhx_agent.conversation import ConversationManager
from xhx_agent.tools.base import StreamEnd, TextDelta


def test_mark_last_user_tail_str_content() -> None:
    msgs = [{"role": "user", "content": "hi"}]
    _mark_last_user_tail_for_cache(msgs)
    assert isinstance(msgs[0]["content"], list)
    assert msgs[0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_mark_last_user_tail_block_content() -> None:
    msgs = [
        {"role": "assistant", "content": "x"},
        {"role": "user", "content": [{"type": "text", "text": "q"}]},
    ]
    _mark_last_user_tail_for_cache(msgs)
    assert msgs[1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    # 空列表安全
    _mark_last_user_tail_for_cache([])


def test_mark_last_tool_for_cache() -> None:
    tools = [{"name": "a"}, {"name": "b"}]
    out = _mark_last_tool_for_cache(tools)
    assert out[-1]["cache_control"] == {"type": "ephemeral"}
    assert _mark_last_tool_for_cache([]) == []


def test_supports_adaptive_thinking() -> None:
    # 返回 bool，不抛错
    assert isinstance(_supports_adaptive_thinking("claude-opus-4-8"), bool)
    assert isinstance(_supports_adaptive_thinking("gpt-4o"), bool)


def test_convert_tools() -> None:
    flat = [{"name": "f", "description": "d", "parameters": {"type": "object"}}]
    out = OpenAICompatClient._convert_tools(flat)
    assert out[0]["type"] == "function" and out[0]["function"]["name"] == "f"
    # 已是嵌套格式则透传
    nested = [{"type": "function", "function": {"name": "g"}}]
    assert OpenAICompatClient._convert_tools(nested) == nested
    # input_schema 兜底
    out2 = OpenAICompatClient._convert_tools([{"name": "h", "input_schema": {"x": 1}}])
    assert out2[0]["function"]["parameters"] == {"x": 1}


def test_error_hierarchy() -> None:
    assert issubclass(AuthenticationError, LLMError)
    assert issubclass(RateLimitError, LLMError)
    assert issubclass(NetworkError, LLMError)
    e = RateLimitError("slow down", retry_after=5.0)
    assert e.retry_after == 5.0


def test_mock_client_stream() -> None:
    client = MockClient()

    async def _run():
        return [e async for e in client.stream(ConversationManager(), system="s")]

    events = asyncio.run(_run())
    assert any(isinstance(e, TextDelta) for e in events)
    assert any(isinstance(e, StreamEnd) for e in events)


def test_create_client_dispatch() -> None:
    assert isinstance(create_client(ProviderConfig(name="m", protocol="mock", model="m")), MockClient)
    compat = create_client(
        ProviderConfig(name="d", protocol="openai-compat", base_url="http://x", model="d", api_key="k")
    )
    assert isinstance(compat, OpenAICompatClient)


def test_resolve_context_window_skips_non_anthropic() -> None:
    cfg = ProviderConfig(name="m", protocol="mock", model="m")
    # 非 anthropic → 直接返回，不改 fetched
    asyncio.run(resolve_context_window(cfg))
    assert cfg._fetched_context_window == 0


def test_resolve_context_window_skips_explicit() -> None:
    cfg = ProviderConfig(name="a", protocol="anthropic", model="claude", api_key="x", context_window=123456)
    asyncio.run(resolve_context_window(cfg))
    # 已有显式值 → 不拉取
    assert cfg._fetched_context_window == 0
