from xhx_agent.runtime.session import SessionEntry, format_follow_up, load_latest_session, record_session


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


def test_format_follow_up_includes_key_fields() -> None:
    entry = SessionEntry(
        run_id="run-9", task="do thing", status="success", verification="passed", changed_files=["x.py"]
    )
    text = format_follow_up(entry)
    assert "run-9" in text
    assert "x.py" in text
    assert "previous" in text.lower()
