"""Model adapters for xhx-agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhx_agent.runtime.profiles import ModelProfile


def build_chat_client(profile: ModelProfile):
    """按 profile.provider 构造支持 chat(messages, tools) 的客户端。"""
    if profile.provider == "mock":
        from xhx_agent.models.mock import MockModelClient
        return MockModelClient()
    if profile.provider == "openai-compatible":
        from xhx_agent.models.openai_compatible import OpenAICompatibleClient
        return OpenAICompatibleClient(base_url=profile.base_url, api_key_env=profile.api_key_env,
                                      model=profile.model, temperature=profile.temperature)
    from xhx_agent.models.types import ModelClientError
    raise ModelClientError(code="unsupported_provider",
        message=f"Unsupported model provider: {profile.provider}", details={"provider": profile.provider})
