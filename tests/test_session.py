from xhx_agent.runtime.session import (
    SessionEntry,
    format_follow_up,
    format_session_meta,
    list_sessions,
    load_latest_session,
    load_session,
    prune_legacy_sessions,
    record_session,
)


class _ResultStub:
    """Minimal RunResult-like object for recording."""

    def __init__(self, run_id, status, verification, changed_files, summary_path):
        self.run_id = run_id
        self.status = status
        self.verification = verification
        self.changed_files = changed_files
        self.summary_path = summary_path


def test_load_latest_returns_none_without_history(tmp_path) -> None:
    assert load_latest_session(tmp_path) is None


def test_record_and_load_latest_session(tmp_path) -> None:
    record_session(tmp_path, "fix bug", _ResultStub("run-1", "success", "passed", ["a.py"], "r1.md"))
    record_session(tmp_path, "add feature", _ResultStub("run-2", "failed", "failed", ["b.py"], "r2.md"))

    latest = load_latest_session(tmp_path)
    assert latest is not None
    assert latest.run_id == "run-2"
    assert latest.task == "add feature"
    assert latest.status == "failed"
    assert latest.changed_files == ["b.py"]


def test_list_and_load_session_by_id(tmp_path) -> None:
    assert list_sessions(tmp_path) == []
    record_session(tmp_path, "t1", _ResultStub("run-1", "success", "passed", [], None))
    record_session(tmp_path, "t2", _ResultStub("run-2", "failed", "failed", ["b.py"], None))

    entries = list_sessions(tmp_path)
    assert [e.run_id for e in entries] == ["run-1", "run-2"]

    found = load_session(tmp_path, "run-1")
    assert found is not None
    assert found.task == "t1"
    assert load_session(tmp_path, "missing") is None


def test_format_follow_up_includes_key_fields() -> None:
    entry = SessionEntry(
        run_id="run-9", task="do thing", status="success", verification="passed", changed_files=["x.py"]
    )
    text = format_follow_up(entry)
    assert "run-9" in text
    assert "x.py" in text
    assert "previous" in text.lower()


def test_transcript_roundtrip(tmp_path) -> None:
    from xhx_agent.runtime.session import load_transcript_messages, save_transcript

    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    rel = save_transcript(tmp_path, "run-7", msgs)
    assert rel.endswith("run-7.json")
    assert load_transcript_messages(tmp_path, rel) == msgs


def test_load_transcript_missing_returns_none(tmp_path) -> None:
    from xhx_agent.runtime.session import load_transcript_messages

    assert load_transcript_messages(tmp_path, ".xhx/sessions/nope.json") is None
    assert load_transcript_messages(tmp_path, None) is None


def test_record_session_persists_transcript_and_mode(tmp_path) -> None:
    class _LoopResult:
        run_id = "run-8"
        status = "success"
        verification = "not_executed"
        changed_files = ["a.py"]
        summary_path = "r.md"
        mode = "loop"
        transcript_path = None  # loop 已自存；record 也兜底处理见下
        messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]

    entry = record_session(tmp_path, "do it", _LoopResult())
    assert entry.mode == "loop"
    assert entry.transcript_path is not None
    from xhx_agent.runtime.session import load_transcript_messages

    assert load_transcript_messages(tmp_path, entry.transcript_path) == _LoopResult.messages


def test_record_session_backward_compatible_stub(tmp_path) -> None:
    # 老式 stub（无 mode/messages/transcript_path）不应报错，transcript_path 留空
    record_session(tmp_path, "x", _ResultStub("run-9", "success", "passed", [], None))
    entry = load_latest_session(tmp_path)
    assert entry is not None and entry.transcript_path is None and entry.mode == ""


def test_list_conversations_collapses_turns_of_one_conversation(tmp_path) -> None:
    from xhx_agent.runtime.session import list_conversations

    # Three turns of one console conversation share a conversation_id.
    record_session(tmp_path, "你好", _ResultStub("run-a1", "success", "passed", [], None), conversation_id="conv-1")
    record_session(
        tmp_path, "你能做什么", _ResultStub("run-a2", "success", "passed", [], None), conversation_id="conv-1"
    )
    record_session(tmp_path, "不要触屏", _ResultStub("run-a3", "success", "passed", [], None), conversation_id="conv-1")
    # A standalone one-shot run (no conversation_id) stands alone.
    record_session(tmp_path, "one-shot", _ResultStub("run-b", "success", "passed", [], None))

    convs = list_conversations(tmp_path)
    # The 3-turn conversation collapses to ONE entry; with the standalone run that's 2 total
    # (whereas list_sessions would show all 4).
    assert len(list_sessions(tmp_path)) == 4
    assert len(convs) == 2

    by_run = {c.run_id: c for c in convs}
    # The conversation collapses to its LATEST run (full transcript) but is titled by its FIRST task.
    assert "run-a3" in by_run
    assert by_run["run-a3"].task == "你好"
    assert "run-b" in by_run
    assert by_run["run-b"].task == "one-shot"


def test_view_log_roundtrip(tmp_path) -> None:
    from xhx_agent.runtime.session import load_view_log, save_view_log

    lines = ["user> hi", "  ⟶ tool search", "assistant> ok"]
    rel = save_view_log(tmp_path, "run-v", lines)
    assert rel.endswith("run-v.view.json")
    assert load_view_log(tmp_path, rel) == lines


def test_load_view_log_missing_returns_none(tmp_path) -> None:
    from xhx_agent.runtime.session import load_view_log

    assert load_view_log(tmp_path, ".xhx/sessions/nope.view.json") is None
    assert load_view_log(tmp_path, None) is None


def test_session_entry_new_fields_default() -> None:
    # Old JSON representation without the new fields
    old_json = (
        '{"run_id": "run-old", "task": "do old", "status": "success", "verification": "passed", "changed_files": []}'
    )
    entry = SessionEntry.model_validate_json(old_json)
    assert entry.view_path is None
    assert entry.turn_count == 0
    assert entry.updated_at is not None
    # Check that updated_at is a valid ISO format string
    from datetime import datetime

    datetime.fromisoformat(entry.updated_at)


def test_record_session_stores_view_path_and_turn_count(tmp_path) -> None:
    record_session(
        tmp_path,
        "t",
        _ResultStub("run-x", "success", "passed", [], None),
        conversation_id="c1",
        view_path=".xhx/sessions/run-x.view.json",
        turn_count=3,
    )
    latest = load_latest_session(tmp_path)
    assert latest is not None
    assert latest.view_path == ".xhx/sessions/run-x.view.json"
    assert latest.turn_count == 3


def test_resolve_run_id_exact_prefix_suffix() -> None:
    from xhx_agent.runtime.session import resolve_run_id

    entries = [
        SessionEntry(run_id="run-12345678", task="t1", status="success"),
        SessionEntry(run_id="run-87654321", task="t2", status="success"),
    ]
    # Exact match
    r, cands = resolve_run_id(entries, "run-12345678")
    assert r == "run-12345678"
    assert cands == []

    # Suffix match (unique)
    r, cands = resolve_run_id(entries, "87654321")
    assert r == "run-87654321"
    assert cands == []

    # Prefix match (unique)
    r, cands = resolve_run_id(entries, "run-123")
    assert r == "run-12345678"
    assert cands == []


def test_resolve_run_id_ambiguous_returns_candidates() -> None:
    from xhx_agent.runtime.session import resolve_run_id

    entries = [
        SessionEntry(run_id="run-12345678", task="t1", status="success"),
        SessionEntry(run_id="run-1234aaaa", task="t2", status="success"),
    ]
    r, cands = resolve_run_id(entries, "run-1234")
    assert r is None
    assert set(cands) == {"run-12345678", "run-1234aaaa"}


def test_resolve_run_id_miss() -> None:
    from xhx_agent.runtime.session import resolve_run_id

    entries = [
        SessionEntry(run_id="run-12345678", task="t1", status="success"),
    ]
    r, cands = resolve_run_id(entries, "nope")
    assert r is None
    assert cands == []


def test_format_session_line_shape() -> None:
    from datetime import UTC, datetime, timedelta

    from xhx_agent.runtime.session import format_session_line

    now = datetime.now(UTC)

    # 1. Just now
    e1 = SessionEntry(run_id="run-12345678", task="task 1", status="success", turn_count=2, updated_at=now.isoformat())
    assert "刚刚" in format_session_line(e1, now)

    # 2. Minutes ago
    e2 = SessionEntry(
        run_id="run-12345678",
        task="task 2",
        status="success",
        turn_count=2,
        updated_at=(now - timedelta(minutes=5)).isoformat(),
    )
    assert "5分钟前" in format_session_line(e2, now)

    # 3. Hours ago
    e3 = SessionEntry(
        run_id="run-12345678",
        task="task 3",
        status="success",
        turn_count=2,
        updated_at=(now - timedelta(hours=3)).isoformat(),
    )
    assert "3小时前" in format_session_line(e3, now)

    # 4. Days ago
    e4 = SessionEntry(
        run_id="run-12345678",
        task="task 4",
        status="success",
        turn_count=2,
        updated_at=(now - timedelta(days=2)).isoformat(),
    )
    assert "2天前" in format_session_line(e4, now)

    # 5. Long task truncation
    long_task = "a" * 100
    e5 = SessionEntry(run_id="run-12345678", task=long_task, status="success", turn_count=2, updated_at=now.isoformat())
    line = format_session_line(e5, now)
    assert "…" in line or "..." in line
    assert "success" in line
    assert "5678" in line


def test_format_session_meta() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    # 1. 5 minutes ago, status="success", turn_count=3, run_id="run-12345678"
    e1 = SessionEntry(
        run_id="run-12345678",
        task="task 1",
        status="success",
        turn_count=3,
        updated_at=(now - timedelta(minutes=5)).isoformat(),
    )
    meta1 = format_session_meta(e1, now)
    assert "5分钟前" in meta1
    assert "success" in meta1
    assert "3轮" in meta1
    assert "5678" in meta1
    assert "task 1" not in meta1  # should not contain task

    # 2. Future time (now + 5 mins) -> "刚刚"
    e2 = SessionEntry(
        run_id="run-12345678",
        task="task 2",
        status="running",
        turn_count=1,
        updated_at=(now + timedelta(minutes=5)).isoformat(),
    )
    meta2 = format_session_meta(e2, now)
    assert "刚刚" in meta2


def test_prune_legacy_sessions(tmp_path) -> None:
    # Empty workspace -> 0
    assert prune_legacy_sessions(tmp_path) == 0

    # Record 3 sessions with view_path, and 2 sessions without view_path
    # (use dict/fake-like result, or minimal result object)
    from xhx_agent.runtime.result import RunResult

    res = RunResult(
        run_id="run-1",
        status="success",
        changed_files=[],
        commands=[],
        verification="passed",
        summary_path="",
        risk_summary=[],
    )
    # 3 with view_path
    record_session(tmp_path, "task 1", res, view_path=".xhx/sessions/run-1.view.json")
    res.run_id = "run-2"
    record_session(tmp_path, "task 2", res, view_path=".xhx/sessions/run-2.view.json")
    res.run_id = "run-3"
    record_session(tmp_path, "task 3", res, view_path=".xhx/sessions/run-3.view.json")

    # 2 without view_path
    res.run_id = "run-4"
    record_session(tmp_path, "task 4", res, view_path=None)
    res.run_id = "run-5"
    record_session(tmp_path, "task 5", res, view_path="")

    # Verify initial length is 5
    assert len(list_sessions(tmp_path)) == 5

    # Run prune
    pruned = prune_legacy_sessions(tmp_path)
    assert pruned == 2

    # Check remaining
    remaining = list_sessions(tmp_path)
    assert len(remaining) == 3
    assert all(e.view_path for e in remaining)
    assert [e.run_id for e in remaining] == ["run-1", "run-2", "run-3"]
