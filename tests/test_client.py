"""新栈 LLM 客户端测试。"""
from __future__ import annotations

from xhx_agent.client import OpenAICompatClient
from xhx_agent.config import ProviderConfig


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
