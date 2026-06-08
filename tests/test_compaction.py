from xhx_agent.context.compiler import _compact_tool_summaries


def test_compact_tool_summaries_under_keep_returns_all() -> None:
    summaries = ["read_file: success: a", "search: success: b"]
    compacted, recent = _compact_tool_summaries(summaries, keep_recent=5)
    assert compacted is None
    assert recent == summaries


def test_compact_tool_summaries_compacts_overflow() -> None:
    summaries = [
        "apply_patch: success: x",
        "apply_patch: success: y",
        "apply_patch: failed: z",
        "search: success: q",
        "read_file: success: r",
        "read_file: success: s",
    ]
    compacted, recent = _compact_tool_summaries(summaries, keep_recent=2)
    assert recent == summaries[-2:]
    assert compacted is not None
    assert "compacted 4 earlier" in compacted
    assert "apply_patch×3" in compacted
    assert "1 failed" in compacted
