"""serialization.py 单测：消息列表 → 各协议格式 + 未配对 tool_use 过滤。"""

from __future__ import annotations

from xhx_agent.conversation import Message, ThinkingBlock, ToolResultBlock, ToolUseBlock
from xhx_agent.serialization import (
    _filter_unresolved_tool_uses,
    build_anthropic_messages,
    build_chat_completion_messages,
    build_messages,
    build_openai_input,
)


def _conv() -> list[Message]:
    return [
        Message(role="user", content="读文件"),
        Message(
            role="assistant",
            content="好的",
            tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="read_file", arguments={"path": "a.py"})],
        ),
        Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content="file body")]),
        Message(role="assistant", content="读完了"),
    ]


def test_anthropic_messages_shape() -> None:
    out = build_anthropic_messages(_conv())
    assert out[0] == {"role": "user", "content": "读文件"}
    # assistant 带 tool_use → content 为 block 列表
    assert out[1]["role"] == "assistant"
    assert any(b["type"] == "tool_use" and b["name"] == "read_file" for b in out[1]["content"])
    # tool_result → user role 的 tool_result block
    assert out[2]["role"] == "user"
    assert out[2]["content"][0]["type"] == "tool_result"


def test_openai_input_shape() -> None:
    out = build_openai_input(_conv())
    assert {"role": "user", "content": "读文件"} in out
    assert any(item.get("type") == "function_call" and item["name"] == "read_file" for item in out)
    assert any(item.get("type") == "function_call_output" and item["call_id"] == "t1" for item in out)


def test_chat_completion_shape() -> None:
    out = build_chat_completion_messages(_conv())
    asst = [m for m in out if m["role"] == "assistant" and m.get("tool_calls")]
    assert asst and asst[0]["tool_calls"][0]["function"]["name"] == "read_file"
    tool_msgs = [m for m in out if m["role"] == "tool"]
    assert tool_msgs and tool_msgs[0]["tool_call_id"] == "t1"


def test_build_messages_dispatch() -> None:
    conv = _conv()
    assert build_messages(conv, "openai") == build_openai_input(conv)
    assert build_messages(conv, "openai-compat") == build_chat_completion_messages(conv)
    assert build_messages(conv, "anthropic") == build_anthropic_messages(conv)
    # 默认回退 anthropic
    assert build_messages(conv, "unknown-proto") == build_anthropic_messages(conv)


def test_filter_drops_fully_unresolved_tool_use() -> None:
    msgs = [
        Message(role="user", content="hi"),
        Message(
            role="assistant",
            content="",
            tool_uses=[ToolUseBlock(tool_use_id="orphan", tool_name="x", arguments={})],
        ),
    ]
    filtered = _filter_unresolved_tool_uses(msgs)
    # 整条未配对的 assistant 消息被删除
    assert all(not m.tool_uses for m in filtered)
    assert len(filtered) == 1


def test_filter_keeps_partial_resolved() -> None:
    msgs = [
        Message(
            role="assistant",
            content="两个调用",
            tool_uses=[
                ToolUseBlock(tool_use_id="ok", tool_name="a", arguments={}),
                ToolUseBlock(tool_use_id="orphan", tool_name="b", arguments={}),
            ],
        ),
        Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="ok", content="done")]),
    ]
    filtered = _filter_unresolved_tool_uses(msgs)
    assert filtered[0].tool_uses[0].tool_use_id == "ok"
    assert len(filtered[0].tool_uses) == 1


def test_anthropic_thinking_block_serialized() -> None:
    msgs = [
        Message(
            role="assistant",
            content="答案",
            thinking_blocks=[ThinkingBlock(thinking="先想想", signature="sig")],
        )
    ]
    out = build_anthropic_messages(msgs)
    assert out[0]["content"][0]["type"] == "thinking"
    assert out[0]["content"][0]["signature"] == "sig"


def test_anthropic_merges_consecutive_user_text() -> None:
    msgs = [Message(role="user", content="第一句"), Message(role="user", content="第二句")]
    out = build_anthropic_messages(msgs)
    assert len(out) == 1
    assert "第一句" in out[0]["content"] and "第二句" in out[0]["content"]
