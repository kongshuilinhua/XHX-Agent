"""context/manager.py 单测：工具结果预算、压缩阈值、恢复附件、auto_compact 编排。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.context.manager import (
    AGGREGATE_CHAR_LIMIT,
    KEEP_RECENT_TURNS,
    PERSISTED_TAG,
    SINGLE_RESULT_CHAR_LIMIT,
    SNIP_BATCH_TURNS,
    SNIPPED_TAG,
    CompactCircuitBreaker,
    ContentReplacementRecord,
    RecoveryState,
    _align_keep_start_to_tool_pair,
    _compute_keep_start_index,
    _count_turns,
    _first_line,
    _group_messages_by_turn,
    _prefix_too_small_to_compact,
    _truncate_by_tokens,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    build_compact_messages,
    build_recovery_attachment,
    clone_replacement_state,
    compute_compact_threshold,
    create_replacement_state,
    ensure_session_dir,
    extract_summary,
    load_replacement_records,
    make_persisted_preview,
    reconstruct_replacement_state,
    should_auto_compact,
)
from xhx_agent.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock
from xhx_agent.tools.base import StreamEnd, TextDelta

# --- 阈值 / 简单纯函数 ---


def test_compact_threshold_and_should() -> None:
    auto_t = compute_compact_threshold(100_000, manual=False)
    man_t = compute_compact_threshold(100_000, manual=True)
    assert man_t > auto_t  # 手动安全边际更小，阈值更高
    assert should_auto_compact(auto_t, 100_000) is True
    assert should_auto_compact(0, 100_000) is False


def test_extract_summary() -> None:
    assert extract_summary("noise <summary>正文</summary> tail") == "正文"
    assert extract_summary("没有标签") == "没有标签"


def test_build_compact_messages() -> None:
    msgs = build_compact_messages("摘要X", attachment="附件", has_keep_tail=True, transcript_path="/t.jsonl")
    assert len(msgs) == 1 and msgs[0].role == "user"
    body = msgs[0].content
    assert "摘要X" in body and "附件" in body and "/t.jsonl" in body and "近期消息" in body


def test_circuit_breaker() -> None:
    b = CompactCircuitBreaker(max_failures=2)
    assert b.is_open() is False
    b.record_failure()
    b.record_failure()
    assert b.is_open() is True
    b.record_success()
    assert b.is_open() is False


def test_count_turns_and_grouping() -> None:
    msgs = [
        Message(role="user", content="q1"),
        Message(role="assistant", content="a1"),
        Message(role="user", content="q2"),
        Message(role="assistant", content="a2"),
    ]
    assert _count_turns(msgs) == 2
    groups = _group_messages_by_turn(msgs)
    assert len(groups) == 2


def test_first_line_and_truncate() -> None:
    assert _first_line("\n  \n第一行\n第二行") == "第一行"
    assert _truncate_by_tokens("", 100) == ""
    long = "x" * 100_000
    out = _truncate_by_tokens(long, 10)
    assert out.endswith("(内容已截断)") and len(out) < len(long)


# --- 替换状态 / 记录持久化 ---


def test_replacement_state_clone_independent() -> None:
    s = create_replacement_state()
    s.seen_ids.add("a")
    s.replacements["a"] = "X"
    c = clone_replacement_state(s)
    c.seen_ids.add("b")
    assert "b" not in s.seen_ids  # 深拷贝互不影响


def test_replacement_records_roundtrip(tmp_path: Path) -> None:
    recs = [ContentReplacementRecord(tool_use_id="t1", replacement="R1")]
    append_replacement_records(tmp_path, recs)
    loaded = load_replacement_records(tmp_path)
    assert loaded and loaded[0].tool_use_id == "t1" and loaded[0].replacement == "R1"
    # 空目录
    assert load_replacement_records(tmp_path / "sub") == []


def test_reconstruct_replacement_state() -> None:
    msgs = [Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content="x")])]
    recs = [ContentReplacementRecord(tool_use_id="t1", replacement="REPL")]
    state = reconstruct_replacement_state(msgs, recs)
    assert state.replacements["t1"] == "REPL"
    assert "t1" in state.seen_ids


# --- Layer 1 工具结果预算 ---


def test_persist_preview_and_single_limit(tmp_path: Path) -> None:
    session_dir = ensure_session_dir(str(tmp_path))
    big = "A" * (SINGLE_RESULT_CHAR_LIMIT + 100)
    conv = ConversationManager()
    conv.history = [
        Message(
            role="assistant",
            content="",
            tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="read_file", arguments={})],
        ),
        Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content=big)]),
    ]
    state = create_replacement_state()
    new_conv, records = apply_tool_result_budget(conv, session_dir, state)
    tr = new_conv.history[1].tool_results[0]
    assert tr.content.startswith(PERSISTED_TAG)  # 超大结果被落盘成预览
    assert records and records[0].tool_use_id == "t1"
    assert (session_dir / "t1.txt").is_file()


def test_make_persisted_preview(tmp_path: Path) -> None:
    fp = tmp_path / "x.txt"
    out = make_persisted_preview("hello" * 1000, fp)
    assert PERSISTED_TAG in out and "x.txt" in out


def test_aggregate_limit_persists(tmp_path: Path) -> None:
    session_dir = ensure_session_dir(str(tmp_path))
    # 两条都不超单条限制，但合计超聚合限制
    chunk = "B" * (AGGREGATE_CHAR_LIMIT // 2 + 50)
    conv = ConversationManager()
    conv.history = [
        Message(
            role="assistant",
            content="",
            tool_uses=[
                ToolUseBlock(tool_use_id="t1", tool_name="x", arguments={}),
                ToolUseBlock(tool_use_id="t2", tool_name="x", arguments={}),
            ],
        ),
        Message(
            role="user",
            content="",
            tool_results=[
                ToolResultBlock(tool_use_id="t1", content=chunk),
                ToolResultBlock(tool_use_id="t2", content=chunk),
            ],
        ),
    ]
    new_conv, records = apply_tool_result_budget(conv, session_dir, create_replacement_state())
    persisted = [tr for tr in new_conv.history[1].tool_results if tr.content.startswith(PERSISTED_TAG)]
    assert persisted  # 至少一条被落盘以满足聚合预算


# --- Pass 3 陈旧裁剪：批量推进 + 决策冻结 ---


def _turn_msgs(idx: int, result_chars: int = 3000) -> list[Message]:
    """一整轮对话：assistant 发起工具调用 → 工具结果 → assistant 收尾（计一轮）。"""
    tid = f"turn{idx}"
    return [
        Message(role="assistant", content="", tool_uses=[ToolUseBlock(tool_use_id=tid, tool_name="x", arguments={})]),
        Message(
            role="user",
            content="",
            tool_results=[ToolResultBlock(tool_use_id=tid, content=f"R{idx}-" + "x" * result_chars)],
        ),
        Message(role="assistant", content=f"done {idx}"),
    ]


def _conv_with_turns(n: int) -> ConversationManager:
    conv = ConversationManager()
    for i in range(1, n + 1):
        conv.history.extend(_turn_msgs(i))
    return conv


def _flat_results(conv: ConversationManager) -> list[tuple[str, str]]:
    return [(tr.tool_use_id, tr.content) for m in conv.history for tr in m.tool_results]


def test_snip_waits_for_batch(tmp_path: Path) -> None:
    """待裁剪轮数不足 SNIP_BATCH_TURNS 时不动前缀（避免每轮滑动边界破坏缓存）。"""
    session_dir = ensure_session_dir(str(tmp_path))
    state = create_replacement_state()
    conv = _conv_with_turns(KEEP_RECENT_TURNS + SNIP_BATCH_TURNS - 1)
    new_conv, records = apply_tool_result_budget(conv, session_dir, state)
    assert not any(c.startswith(SNIPPED_TAG) for _, c in _flat_results(new_conv))
    assert state.snipped_through_turn == 0
    assert records == []


def test_snip_batch_freezes_decisions(tmp_path: Path) -> None:
    """攒满批量后一次性裁剪；决策冻结进 state + 持久化记录，重复调用输出逐字一致。"""
    session_dir = ensure_session_dir(str(tmp_path))
    state = create_replacement_state()
    total = KEEP_RECENT_TURNS + SNIP_BATCH_TURNS
    conv = _conv_with_turns(total)
    new_conv, records = apply_tool_result_budget(conv, session_dir, state)

    assert state.snipped_through_turn == SNIP_BATCH_TURNS
    snipped_ids = {r.tool_use_id for r in records}
    assert "turn1" in snipped_ids and state.replacements["turn1"].startswith(SNIPPED_TAG)
    # 最近一轮的结果原样保留
    assert dict(_flat_results(new_conv))[f"turn{total}"].startswith(f"R{total}-")

    # 再次调用：无新记录，序列化内容逐字一致（前缀缓存命中的前提）
    again, records2 = apply_tool_result_budget(conv, session_dir, state)
    assert records2 == []
    assert _flat_results(again) == _flat_results(new_conv)


def test_snip_boundary_stable_between_batches(tmp_path: Path) -> None:
    """两次批量之间追加新轮次：已有前缀逐字不变，直到再攒满一批才推进。"""
    session_dir = ensure_session_dir(str(tmp_path))
    state = create_replacement_state()
    total = KEEP_RECENT_TURNS + SNIP_BATCH_TURNS
    conv = _conv_with_turns(total)
    first, _ = apply_tool_result_budget(conv, session_dir, state)
    baseline = _flat_results(first)

    for i in range(1, SNIP_BATCH_TURNS):
        conv.history.extend(_turn_msgs(total + i))
        new_conv, recs = apply_tool_result_budget(conv, session_dir, state)
        assert recs == []
        assert _flat_results(new_conv)[: len(baseline)] == baseline

    conv.history.extend(_turn_msgs(total + SNIP_BATCH_TURNS))
    _, recs = apply_tool_result_budget(conv, session_dir, state)
    assert recs  # 攒满一批，一次性推进
    assert state.snipped_through_turn == 2 * SNIP_BATCH_TURNS


def test_snip_cursor_self_heals_after_history_shrinks(tmp_path: Path) -> None:
    """历史被压缩/回退缩短后游标自愈回落，不会永久挡住后续裁剪。"""
    state = create_replacement_state()
    state.snipped_through_turn = 50
    conv = _conv_with_turns(3)
    apply_tool_result_budget(conv, ensure_session_dir(str(tmp_path)), state)
    assert state.snipped_through_turn == 0


def test_clone_preserves_snip_cursor() -> None:
    s = create_replacement_state()
    s.snipped_through_turn = 7
    assert clone_replacement_state(s).snipped_through_turn == 7


# --- keep_start 对齐 ---


def test_compute_keep_start_and_align() -> None:
    msgs = [Message(role="user" if i % 2 == 0 else "assistant", content=f"m{i}") for i in range(20)]
    ks = _compute_keep_start_index(msgs)
    assert 0 <= ks <= len(msgs)
    # 对齐：keep_start 落在带 tool_result 的 user 上，应回退到配对 assistant
    paired = [
        Message(role="assistant", content="", tool_uses=[ToolUseBlock(tool_use_id="t1", tool_name="x", arguments={})]),
        Message(role="user", content="", tool_results=[ToolResultBlock(tool_use_id="t1", content="r")]),
    ]
    assert _align_keep_start_to_tool_pair(paired, 1) == 0


def test_prefix_too_small() -> None:
    assert _prefix_too_small_to_compact([]) is True
    assert _prefix_too_small_to_compact([Message(role="user", content="x")]) is True
    big = [Message(role="user", content="字" * 10_000)]
    assert _prefix_too_small_to_compact(big) is False


# --- RecoveryState / build_recovery_attachment ---


def test_recovery_state_and_attachment() -> None:
    rs = RecoveryState()
    rs.record_file_read("a.py", "print(1)")
    rs.record_skill_invocation("deploy", "步骤一二三")
    assert len(rs.snapshot_files(5)) == 1
    assert len(rs.snapshot_skills()) == 1
    att = build_recovery_attachment(rs, [{"name": "Bash", "description": "run shell\nmore"}])
    assert "a.py" in att and "deploy" in att and "Bash" in att
    # 全空 → 返回空串
    assert build_recovery_attachment(None, None) == ""


# --- auto_compact 编排 ---


class _SummaryClient:
    async def stream(self, conversation, system=None):
        yield TextDelta(text="<summary>这是压缩后的结构化摘要</summary>")
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=5)


def test_auto_compact_below_threshold_returns_none(tmp_path: Path) -> None:
    conv = ConversationManager()
    conv.history = [Message(role="user", content="hi")]
    result = asyncio.run(
        auto_compact(conv, _SummaryClient(), context_window=200_000, session_dir=tmp_path, manual=False)
    )
    assert result is None  # 远未到阈值


def test_auto_compact_manual_full_path(tmp_path: Path) -> None:
    session_dir = ensure_session_dir(str(tmp_path))
    conv = ConversationManager()
    # 足够长：前缀大于摘要门槛（>2000 token）、且尾部有至少 5 条原样保留
    conv.history = [
        Message(role="user" if i % 2 == 0 else "assistant", content=f"消息{i} 内容 " * 400) for i in range(14)
    ]
    from xhx_agent.context.manager import CompactEvent

    result = asyncio.run(
        auto_compact(conv, _SummaryClient(), context_window=200_000, session_dir=session_dir, manual=True)
    )
    assert isinstance(result, CompactEvent)
    assert result.boundary is not None
    assert "结构化摘要" in result.boundary.summary
    # 历史已被替换：第一条是摘要 user 消息
    assert conv.history[0].role == "user"
