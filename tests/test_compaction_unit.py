"""context/compaction.py 单测：token 估算、预算推导、消息压缩、文件操作提取。"""

from __future__ import annotations

from xhx_agent.context.compaction import (
    _SUMMARY_PREFIX,
    _estimate_message_tokens,
    _estimate_single_message_tokens,
    _extract_file_ops,
    budget_for_window,
    compact_messages,
)


def test_estimate_tokens() -> None:
    msgs = [{"role": "user", "content": "hello world"}, {"role": "assistant", "content": "hi"}]
    assert _estimate_message_tokens(msgs) > 0
    assert _estimate_single_message_tokens({"role": "user", "content": ""}) == 0
    # tool_calls 的 arguments 也计入
    tc_msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "x", "arguments": '{"a": "b" * 100}'}}],
    }
    assert _estimate_single_message_tokens(tc_msg) > 0


def test_budget_for_window() -> None:
    threshold, keep = budget_for_window(128_000)
    assert threshold > 0 and keep > 0 and threshold < 128_000
    # 0 或负数走默认窗口
    t2, k2 = budget_for_window(0)
    assert t2 > 0 and k2 > 0


def test_compact_below_threshold_returns_same() -> None:
    msgs = [{"role": "user", "content": "short"}]
    out = compact_messages(msgs, lambda _p: "SUMMARY", max_tokens=10_000)
    assert out == msgs  # 未超阈值，原样返回


def test_compact_forced_produces_summary() -> None:
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"消息{i} " * 20} for i in range(6)]
    out = compact_messages(
        msgs,
        lambda _p: "这是摘要",
        keep_recent=1,
        keep_recent_tokens=1,
        force=True,
    )
    # 压缩后应短于原文，且含摘要前缀
    assert len(out) < len(msgs)
    assert any(_SUMMARY_PREFIX in str(m.get("content", "")) for m in out)


def test_compact_preserves_system_prefix() -> None:
    msgs = [{"role": "system", "content": "sys"}] + [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"x{i} " * 20} for i in range(6)
    ]
    out = compact_messages(msgs, lambda _p: "S", keep_recent=1, keep_recent_tokens=1, force=True)
    assert out[0]["role"] == "system"


def test_extract_file_ops_from_tool_calls() -> None:
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
            ],
        }
    ]
    read_files, _modified = _extract_file_ops(msgs)
    assert "a.py" in read_files
