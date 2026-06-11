from xhx_agent.runtime.session import (
    SessionEntry,
    format_follow_up,
    list_sessions,
    load_latest_session,
    load_session,
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
