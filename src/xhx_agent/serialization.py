from __future__ import annotations

import json
from typing import Any

from xhx_agent.conversation import Message


def _filter_unresolved_tool_uses(messages: list[Message]) -> list[Message]:
    """清理未配对的 tool_use（对标 Claude Code filterUnresolvedToolUses）。

    收集全局 tool_use id 与 tool_result id，找出没有对应结果的 tool_use：
    - 若 assistant 消息的 **所有** tool_use 都未配对 → 整条删除
    - 若 **部分** 未配对 → 仅剥离未配对的那几个 tool_use，保留消息和已完成的

    不添加合成占位符——这是中断时已实时生成 tool_result 之后的安全网。
    """
    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for m in messages:
        for tu in m.tool_uses:
            tool_use_ids.add(tu.tool_use_id)
        for tr in m.tool_results:
            tool_result_ids.add(tr.tool_use_id)

    unresolved = tool_use_ids - tool_result_ids
    if not unresolved:
        return list(messages)

    filtered: list[Message] = []
    for m in messages:
        if not m.tool_uses:
            filtered.append(m)
            continue

        # 过滤掉未配对的 tool_use
        resolved = [tu for tu in m.tool_uses if tu.tool_use_id not in unresolved]
        if not resolved:
            # 全部未配对 → 删掉整条消息
            continue
        if len(resolved) != len(m.tool_uses):
            # 部分未配对 → 保留消息，只保留已完成的 tool_use
            filtered.append(
                Message(
                    role=m.role,
                    content=m.content,
                    tool_uses=resolved,
                    thinking_blocks=list(m.thinking_blocks),
                )
            )
        else:
            filtered.append(m)
    return filtered


def build_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
    # 先过滤未配对的 tool_use，再序列化
    messages = _filter_unresolved_tool_uses(messages)
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses or m.thinking_blocks:
            content: list[dict[str, Any]] = []
            for tb in m.thinking_blocks:
                content.append(
                    {
                        "type": "thinking",
                        "thinking": tb.thinking,
                        "signature": tb.signature,
                    }
                )
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tu in m.tool_uses:
                content.append(
                    {
                        "type": "tool_use",
                        "id": tu.tool_use_id,
                        "name": tu.tool_name,
                        "input": tu.arguments,
                    }
                )
            if not content:
                content.append({"type": "text", "text": ""})
            result.append({"role": "assistant", "content": content})
        elif m.tool_results:
            content = []
            for tr in m.tool_results:
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_use_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    }
                )
            result.append({"role": "user", "content": content})
        else:
            if m.role == "user" and result and result[-1]["role"] == "user" and isinstance(result[-1]["content"], str):
                result[-1]["content"] = result[-1]["content"] + "\n" + m.content
            else:
                result.append({"role": m.role, "content": m.content})
    return result


def build_openai_input(messages: list[Message]) -> list[dict[str, Any]]:
    messages = _filter_unresolved_tool_uses(messages)
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses:
            if m.content:
                result.append({"role": "assistant", "content": m.content})
            for tu in m.tool_uses:
                result.append(
                    {
                        "type": "function_call",
                        "name": tu.tool_name,
                        "call_id": tu.tool_use_id,
                        "arguments": json.dumps(tu.arguments),
                    }
                )
        elif m.tool_results:
            for tr in m.tool_results:
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": tr.tool_use_id,
                        "output": tr.content,
                    }
                )
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def build_chat_completion_messages(messages: list[Message]) -> list[dict[str, Any]]:
    """OpenAI Chat Completions 格式。

    - 用户消息：{"role": "user", "content": "..."}
    - 助手文本+工具调用：{"role": "assistant", "content": "...", "tool_calls": [...]}
    - 工具结果：{"role": "tool", "tool_call_id": "...", "content": "..."}
    - thinking 块被跳过（Chat Completions 不支持）。
    """
    messages = _filter_unresolved_tool_uses(messages)
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.tool_uses:
            tool_calls = []
            for tu in m.tool_uses:
                tool_calls.append(
                    {
                        "id": tu.tool_use_id,
                        "type": "function",
                        "function": {
                            "name": tu.tool_name,
                            "arguments": json.dumps(tu.arguments),
                        },
                    }
                )
            result.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": tool_calls,
                }
            )
        elif m.tool_results:
            for tr in m.tool_results:
                result.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_use_id,
                        "content": tr.content,
                    }
                )
        else:
            result.append({"role": m.role, "content": m.content})
    return result


def build_messages(messages: list[Message], protocol: str = "anthropic") -> list[dict[str, Any]]:
    if protocol == "openai":
        return build_openai_input(messages)
    if protocol == "openai-compat":
        return build_chat_completion_messages(messages)
    return build_anthropic_messages(messages)
