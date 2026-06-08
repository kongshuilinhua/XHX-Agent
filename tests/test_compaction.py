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


def test_context_pack_compacts_old_tool_summaries(tmp_path) -> None:
    from xhx_agent.context.compiler import MAX_TOOL_SUMMARIES, compile_context_pack
    from xhx_agent.repo_intel.scanner import scan_project

    summaries = [f"apply_patch: success: change {i}" for i in range(MAX_TOOL_SUMMARIES + 3)]
    scan = scan_project(tmp_path)
    pack = compile_context_pack(workspace=tmp_path, task="continue work", scan=scan, tool_summaries=summaries)

    tool_item = next((item for item in pack.items if item.kind == "tool_results"), None)
    assert tool_item is not None
    # older 3 are compacted into one stat line; the most recent 12 stay verbatim
    assert tool_item.content.startswith("[compacted 3 earlier")
    assert "- apply_patch: success: change 14" in tool_item.content
    assert "- apply_patch: success: change 0" not in tool_item.content


def test_compact_with_summarizer_uses_it() -> None:
    summaries = [f"apply_patch: success: {i}" for i in range(5)]
    compacted, recent = _compact_tool_summaries(summaries, keep_recent=2, summarizer=lambda older: f"SEM {len(older)}")
    assert recent == summaries[-2:]
    assert compacted == "[summary] SEM 3"


def test_compact_summarizer_failure_falls_back_to_heuristic() -> None:
    summaries = [f"apply_patch: success: {i}" for i in range(5)]

    def boom(_older: list[str]) -> str:
        raise RuntimeError("summarizer down")

    compacted, recent = _compact_tool_summaries(summaries, keep_recent=2, summarizer=boom)
    assert recent == summaries[-2:]
    assert compacted is not None
    assert "compacted 3 earlier" in compacted


def test_mock_summarize_is_deterministic() -> None:
    from xhx_agent.models.mock import MockModelClient

    text = "apply_patch: success: x\nsearch: failed: y"
    out = MockModelClient().summarize(text)
    assert "2 tool step" in out


def test_compile_context_pack_uses_history_summarizer(tmp_path) -> None:
    from xhx_agent.context.compiler import MAX_TOOL_SUMMARIES, compile_context_pack
    from xhx_agent.repo_intel.scanner import scan_project

    summaries = [f"apply_patch: success: {i}" for i in range(MAX_TOOL_SUMMARIES + 2)]
    scan = scan_project(tmp_path)
    pack = compile_context_pack(
        workspace=tmp_path,
        task="t",
        scan=scan,
        tool_summaries=summaries,
        history_summarizer=lambda older: f"semantic of {len(older)}",
    )
    tool_item = next(item for item in pack.items if item.kind == "tool_results")
    assert tool_item.content.startswith("[summary] semantic of 2")


def test_make_history_summarizer_for_mock_profile(tmp_path) -> None:
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.runtime.profiles import ModelProfile

    summarizer = RuntimeApp(tmp_path)._make_history_summarizer(ModelProfile(provider="mock"))
    out = summarizer(["a: success: x", "b: failed: y"])
    assert "2 tool step" in out
