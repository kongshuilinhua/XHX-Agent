"""新栈 LLM 客户端测试。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from xhx_agent.client import OpenAICompatClient
from xhx_agent.config import ProviderConfig
from xhx_agent.conversation import ConversationManager
from xhx_agent.tools.base import StreamEnd


def _client() -> OpenAICompatClient:
    return OpenAICompatClient(
        ProviderConfig(protocol="openai-compat", base_url="http://x/v1", model="m", api_key="sk-test")
    )


def test_convert_tools_passes_through_nested_schema() -> None:
    # Agent 用 get_all_schemas("openai-compat") 产出的已是 Chat Completions 嵌套格式，
    # _convert_tools 必须原样透传，否则取 t["name"] 会抛 KeyError（真模型必崩）。
    nested = [
        {
            "type": "function",
            "function": {"name": "ReadFile", "description": "读文件", "parameters": {"type": "object"}},
        }
    ]

    out = _client()._convert_tools(nested)

    assert out == nested
    assert out[0]["function"]["name"] == "ReadFile"


def test_convert_tools_wraps_flat_schema() -> None:
    flat = [{"name": "Glob", "description": "匹配文件", "input_schema": {"type": "object"}}]

    out = _client()._convert_tools(flat)

    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "Glob"
    assert out[0]["function"]["parameters"] == {"type": "object"}


def _content_chunk(text: str, finish_reason: str | None = None, usage: Any = None) -> SimpleNamespace:
    choice = SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def _usage(prompt: int, completion: int, cached: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )


def _stream_events(chunks: list[SimpleNamespace]) -> list[Any]:
    client = _client()

    async def _fake_create(**kwargs: Any) -> Any:
        async def _iter() -> Any:
            for chunk in chunks:
                yield chunk

        return _iter()

    client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=_fake_create)))
    conv = ConversationManager()
    conv.add_user_message("hi")

    async def _run() -> list[Any]:
        return [event async for event in client.stream(conv)]

    return asyncio.run(_run())


def test_stream_reads_usage_from_final_choices_chunk() -> None:
    # DeepSeek 把 usage 挂在最后一个带 finish_reason 的 chunk 上（而非 OpenAI 的末尾空 choices chunk），
    # StreamEnd 必须仍拿到真实计数，否则 token 统计与 auto-compact 基线全归零。
    events = _stream_events(
        [
            _content_chunk("he"),
            _content_chunk("llo", finish_reason="stop", usage=_usage(prompt=100, completion=7, cached=20)),
        ]
    )

    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"
    assert ends[0].input_tokens == 80  # prompt 含缓存 token，需扣除 cache_read 保持可加性
    assert ends[0].cache_read == 20
    assert ends[0].output_tokens == 7


def test_stream_reads_usage_from_trailing_empty_choices_chunk() -> None:
    # OpenAI 风格：usage 在末尾一个空 choices 的专属 chunk 里。
    events = _stream_events(
        [
            _content_chunk("hi", finish_reason="stop"),
            SimpleNamespace(choices=[], usage=_usage(prompt=50, completion=3)),
        ]
    )

    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].input_tokens == 50
    assert ends[0].output_tokens == 3


def test_stream_reads_cache_hit_from_top_level_usage_field() -> None:
    # DeepSeek 等 provider 不给 prompt_tokens_details，缓存命中数在 usage 顶层的
    # prompt_cache_hit_tokens；解析需按位置容错，否则命中率观测恒为 0。
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=5,
        prompt_cache_hit_tokens=64,
        prompt_cache_miss_tokens=36,
    )
    events = _stream_events([_content_chunk("ok", finish_reason="stop", usage=usage)])

    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].cache_read == 64
    assert ends[0].input_tokens == 36  # prompt 含缓存 token，扣除后保持可加性


def test_stream_without_usage_still_emits_single_end() -> None:
    # provider 全程不回传 usage：仍要有且仅有一个 StreamEnd 收尾（计数交由本地估算）。
    events = _stream_events([_content_chunk("hi", finish_reason="stop")])

    ends = [e for e in events if isinstance(e, StreamEnd)]
    assert len(ends) == 1
    assert ends[0].stop_reason == "end_turn"
    assert ends[0].input_tokens == 0
    assert ends[0].output_tokens == 0
