"""消息历史压缩（microcompact）：长 loop 里把旧的中间历史压成一句摘要，保留 system + 近期若干条。

铁律：**绝不破坏 OpenAI 消息有效性**——每个 `tool` 消息必须有其前驱 assistant 的 `tool_calls` 配对。
因此保留的尾部**必须从非 tool 消息开始**（否则会留下孤儿 tool 消息→ API 报错）；被压缩掉的整段换成一条摘要消息。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from xhx_agent.orchestrators._toolturn import _estimate_message_tokens

DEFAULT_COMPACT_THRESHOLD_TOKENS = 12_000
DEFAULT_KEEP_RECENT_MESSAGES = 6
DEFAULT_KEEP_RECENT_TOKENS = 2_000
DEFAULT_RESERVE_TOKENS = 1_000
# 压缩时给模型输出 + 摘要本身预留的空间上限（对标 Claude 的 reserve；小窗口按 1/4 缩放）。
DEFAULT_OUTPUT_RESERVE_TOKENS = 16_000
_SUMMARY_PREFIX = "[Earlier turns compacted to save context]"


def budget_for_window(context_window: int) -> tuple[int, int]:
    """由模型上下文窗口推导 (压缩触发阈值, 保留近期 token)。对标 Claude / pi 的「阈值=f(窗口)」。

    阈值 = 窗口 − 输出预留 − 安全 buffer；输出预留按 min(窗口//4, 16k)，避免小窗口预留过头。
    keep_recent_tokens = min(窗口//3, 24k)：长窗口下保留更多近期原文，短窗口按比例缩。
    窗口 ≤0（未知）时回退 128k 基准，绝不退回写死的 12k。
    """
    window = context_window if context_window > 0 else _DEFAULT_BUDGET_WINDOW
    reserve = min(window // 4, DEFAULT_OUTPUT_RESERVE_TOKENS)
    threshold = max(window - reserve - DEFAULT_RESERVE_TOKENS, 4_000)
    keep_recent = min(window // 3, 24_000)
    return threshold, keep_recent


_DEFAULT_BUDGET_WINDOW = 128_000


def _estimate_single_message_tokens(message: dict[str, Any]) -> int:
    from xhx_agent.context.compiler import _estimate_tokens

    total = _estimate_tokens(str(message.get("content") or ""))
    for tc in message.get("tool_calls") or []:
        total += _estimate_tokens(str(tc.get("function", {}).get("arguments", "")))
    return total


def _extract_file_ops(messages: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """从被压缩的消息中提取读过和改过的文件列表。"""
    read_files = set()
    modified_files = set()
    patch_regex = re.compile(r"\*\*\*\s*Update\s*File:\s*([^\n\r]+)")

    for msg in messages:
        content = str(msg.get("content") or "")

        # 1. 恢复历史累积的文件列表
        if content.startswith(_SUMMARY_PREFIX):
            read_match = re.search(r"<read-files>(.*?)</read-files>", content, re.DOTALL)
            if read_match:
                for line in read_match.group(1).splitlines():
                    path = line.strip()
                    if path:
                        read_files.add(path)
            mod_match = re.search(r"<modified-files>(.*?)</modified-files>", content, re.DOTALL)
            if mod_match:
                for line in mod_match.group(1).splitlines():
                    path = line.strip()
                    if path:
                        modified_files.add(path)

        # 2. 从 tool_calls 提取
        for tc in msg.get("tool_calls") or []:
            name = tc.get("function", {}).get("name")
            args_str = tc.get("function", {}).get("arguments") or ""
            try:
                args = json.loads(args_str) if isinstance(args_str, str) and args_str.strip() else args_str
            except Exception:
                args = {}
            if not isinstance(args, dict):
                args = {}

            if name == "read_file":
                p = args.get("path")
                if p:
                    read_files.add(str(p))
            elif name == "apply_patch":
                p = args.get("path")
                if p:
                    modified_files.add(str(p))
                patch = args.get("patch")
                if patch:
                    for f in patch_regex.findall(str(patch)):
                        modified_files.add(f.strip())

    return read_files, modified_files


def _extract_previous_summary(messages: list[dict[str, Any]]) -> str | None:
    """提取之前的 compaction 摘要，去掉文件列表等标签。"""
    for msg in messages:
        content = str(msg.get("content") or "")
        if content.startswith(_SUMMARY_PREFIX):
            text = content.removeprefix(_SUMMARY_PREFIX).strip()
            text = re.sub(r"<read-files>.*?</read-files>", "", text, flags=re.DOTALL)
            text = re.sub(r"<modified-files>.*?</modified-files>", "", text, flags=re.DOTALL)
            return text.strip()
    return None


def _serialize_for_summarize(messages: list[dict[str, Any]]) -> str:
    """把被压缩的消息渲染成一段纯文本。"""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "?")
        if role == "system":
            continue
        content = str(msg.get("content") or "").strip()
        if content.startswith(_SUMMARY_PREFIX):
            continue
        tool_calls = msg.get("tool_calls") or []

        if role == "user":
            lines.append(f"[User]: {content}")
        elif role == "assistant":
            if content:
                lines.append(f"[Assistant]: {content}")
            if tool_calls:
                calls = []
                for tc in tool_calls:
                    name = tc.get("function", {}).get("name")
                    args = tc.get("function", {}).get("arguments")
                    calls.append(f"{name}({args})")
                lines.append(f"[Assistant tool calls]: {'; '.join(calls)}")
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            if len(content) > 2000:
                content = content[:2000] + f"\n...<truncated {len(content) - 2000} chars>"
            lines.append(f"[Tool result id={tool_call_id}]: {content}")
    return "\n".join(lines)


SUMMARIZATION_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish?]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user, or "(none)"]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue, or "(none)"]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

UPDATE_SUMMARIZATION_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


def compact_messages(
    messages: list[dict[str, Any]],
    summarize: Callable[[str], str],
    *,
    max_tokens: int = DEFAULT_COMPACT_THRESHOLD_TOKENS,
    keep_recent: int = DEFAULT_KEEP_RECENT_MESSAGES,
    keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
    force: bool = False,
    custom_instructions: str | None = None,
) -> list[dict[str, Any]]:
    """估算 token 超过 max_tokens (或 force=True) 时，把中间旧历史压成一条摘要消息；否则原样返回。"""
    total_tokens = _estimate_message_tokens(messages)
    if total_tokens <= max_tokens and not force:
        return messages

    head = 0
    while head < len(messages) and messages[head].get("role") == "system":
        head += 1
    system_msgs = messages[:head]
    body = messages[head:]

    # 从尾部向后累加估算 token，直到达到 keep_recent_tokens
    accumulated_tokens = 0
    token_cut = len(body)
    for i in range(len(body) - 1, -1, -1):
        accumulated_tokens += _estimate_single_message_tokens(body[i])
        if accumulated_tokens >= keep_recent_tokens:
            token_cut = i
            break

    # 实际 cut 点取 keep_recent 和 token_cut 的最小值
    cut = min(len(body) - keep_recent, token_cut)
    cut = max(0, cut)

    # 避免 orphaned tool 消息 (切点不能落在 tool 消息上)
    while cut < len(body) and body[cut].get("role") == "tool":
        cut += 1

    compacted = body[:cut]
    tail = body[cut:]

    if not compacted:
        return messages

    read_files, modified_files = _extract_file_ops(compacted)
    previous_summary = _extract_previous_summary(compacted)

    conversation_text = _serialize_for_summarize(compacted)

    prompt_parts = []
    prompt_parts.append(f"<conversation>\n{conversation_text}\n</conversation>")
    if previous_summary:
        prompt_parts.append(f"<previous-summary>\n{previous_summary}\n</previous-summary>")

    base_prompt = UPDATE_SUMMARIZATION_PROMPT if previous_summary else SUMMARIZATION_PROMPT
    if custom_instructions:
        base_prompt = f"{base_prompt}\n\nAdditional focus: {custom_instructions}"

    prompt_parts.append(base_prompt)
    prompt = "\n\n".join(prompt_parts)

    summary_text = summarize(prompt).strip()

    file_info = []
    if read_files:
        file_info.append("<read-files>")
        file_info.extend(sorted(read_files))
        file_info.append("</read-files>\n")
    if modified_files:
        file_info.append("<modified-files>")
        file_info.extend(sorted(modified_files))
        file_info.append("</modified-files>\n")

    if file_info:
        summary_text = f"{summary_text}\n\n" + "\n".join(file_info).strip()

    summary_msg: dict[str, Any] = {"role": "user", "content": f"{_SUMMARY_PREFIX}\n{summary_text}"}
    return [*system_msgs, summary_msg, *tail]
