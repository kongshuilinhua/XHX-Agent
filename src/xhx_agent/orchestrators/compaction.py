"""消息历史压缩（microcompact）：长 loop 里把旧的中间历史压成一句摘要，保留 system + 近期若干条。

铁律：**绝不破坏 OpenAI 消息有效性**——每个 `tool` 消息必须有其前驱 assistant 的 `tool_calls` 配对。
因此保留的尾部**必须从非 tool 消息开始**（否则会留下孤儿 tool 消息→ API 报错）；被压缩掉的整段换成一条摘要消息。
摘要只在估算 token 超阈值时才触发（额外一次便宜模型调用），否则原样返回——零额外成本。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from xhx_agent.orchestrators._toolturn import _estimate_message_tokens

DEFAULT_COMPACT_THRESHOLD_TOKENS = 12_000
DEFAULT_KEEP_RECENT_MESSAGES = 6
_SUMMARY_PREFIX = "[Earlier turns compacted to save context]"


def compact_messages(
    messages: list[dict[str, Any]],
    summarize: Callable[[str], str],
    *,
    max_tokens: int = DEFAULT_COMPACT_THRESHOLD_TOKENS,
    keep_recent: int = DEFAULT_KEEP_RECENT_MESSAGES,
) -> list[dict[str, Any]]:
    """估算 token 超过 max_tokens 时把中间旧历史压成一条摘要消息；否则原样返回（不调用 summarize）。

    返回的新消息列表保持有效：前导 system 原样 + 一条摘要 + 从非 tool 消息起的近期尾部。
    """
    if _estimate_message_tokens(messages) <= max_tokens:
        return messages

    head = 0
    while head < len(messages) and messages[head].get("role") == "system":
        head += 1
    system_msgs = messages[:head]
    body = messages[head:]

    # 切点：尾部至少 keep_recent 条；若切点落在 tool 消息上则后移，保证尾首非 tool（避免孤儿）。
    cut = max(0, len(body) - keep_recent)
    while cut < len(body) and body[cut].get("role") == "tool":
        cut += 1
    compacted, tail = body[:cut], body[cut:]
    if not compacted:
        return messages  # 近期消息已占满，没有可压缩的旧历史

    summary_text = summarize(_render_for_summary(compacted)).strip()
    summary_msg: dict[str, Any] = {"role": "user", "content": f"{_SUMMARY_PREFIX}\n{summary_text}"}
    return [*system_msgs, summary_msg, *tail]


def _render_for_summary(messages: list[dict[str, Any]]) -> str:
    """把被压缩的消息渲染成一段纯文本喂给 summarize（含工具调用名，便于摘要保真）。"""
    lines: list[str] = []
    for message in messages:
        role = message.get("role", "?")
        content = str(message.get("content") or "")
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls)
            content = f"{content} [called tools: {names}]".strip()
        lines.append(f"{role}: {content}")
    return "\n".join(lines)
