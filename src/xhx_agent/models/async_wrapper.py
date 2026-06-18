"""同步客户端的异步包装器：使现有 sync OpenAI-compatible 客户端适配 Agent 的 async 接口。"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

from xhx_agent.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete


class AsyncClientWrapper:
    """将同步 chat 客户端包装为 Agent 期望的异步流式接口。"""

    def __init__(self, sync_client: Any) -> None:
        self._client = sync_client
        self._max_output_tokens: int = 4096

    def set_max_output_tokens(self, tokens: int) -> None:
        self._max_output_tokens = tokens

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """在默认 executor 里同步调用 chat，然后以异步流的方式产出事件。"""
        # 如果有 system prompt，前置到 messages
        api_messages = list(messages)
        if system:
            api_messages = [{"role": "system", "content": system}] + api_messages

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.chat(api_messages, tools),
            )
        except Exception as e:
            yield StreamEnd(
                stop_reason="error",
                input_tokens=0,
                output_tokens=0,
            )
            return

        # 兼容 Pydantic BaseModel 和 dict 两种返回类型
        if hasattr(result, "content"):
            text = result.content or ""
            tool_calls_raw = getattr(result, "tool_calls", None) or []
            usage = getattr(result, "usage", None)
        else:
            text = result.get("content") or ""
            tool_calls_raw = result.get("tool_calls") or []
            usage = result.get("usage")

        # 产出文本
        if text:
            yield TextDelta(text=text)

        # 产出 tool calls
        for tc in tool_calls_raw:
            # XHX-Agent ToolCall: .name + .arguments 直接在对象上
            if hasattr(tc, "name") and not hasattr(tc, "function"):
                fn_name = tc.name or ""
                fn_args = tc.arguments or {}
                tc_id = tc.id if hasattr(tc, "id") else ""
            # Anthropic 格式: .function.name + .function.arguments
            elif hasattr(tc, "function"):
                fn_name = tc.function.name if hasattr(tc.function, "name") else ""
                fn_args = tc.function.arguments if hasattr(tc.function, "arguments") else {}
                tc_id = tc.id if hasattr(tc, "id") else ""
            elif isinstance(tc, dict):
                fn = tc.get("function", {})
                fn_name = fn.get("name", "")
                fn_args = fn.get("arguments", {})
                tc_id = tc.get("id", "")
            else:
                continue

            if isinstance(fn_args, str):
                import json
                try:
                    fn_args = json.loads(fn_args)
                except json.JSONDecodeError:
                    fn_args = {}

            yield ToolCallComplete(
                tool_id=tc_id,
                tool_name=fn_name,
                arguments=fn_args,
            )

        # 产出结束事件
        input_tokens = 0
        output_tokens = 0
        if usage:
            # XHX-Agent TokenUsage: .prompt / .completion
            if hasattr(usage, "prompt"):
                input_tokens = usage.prompt
                output_tokens = getattr(usage, "completion", 0)
            elif hasattr(usage, "prompt_tokens"):
                input_tokens = usage.prompt_tokens
                output_tokens = getattr(usage, "completion_tokens", 0)
            elif isinstance(usage, dict):
                input_tokens = usage.get("prompt_tokens", 0) or usage.get("prompt", 0)
                output_tokens = usage.get("completion_tokens", 0) or usage.get("completion", 0)

        yield StreamEnd(
            stop_reason="end_turn" if not tool_calls_raw else "tool_use",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


def wrap_sync_client(sync_client: Any) -> AsyncClientWrapper:
    """工厂函数：包装同步客户端为异步版本。"""
    return AsyncClientWrapper(sync_client)
