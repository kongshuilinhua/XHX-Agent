from xhx_agent.runtime.result import RunResult


def _base(**kw):
    return RunResult(
        run_id="r1",
        status="success",
        changed_files=[],
        commands=[],
        verification="skipped",
        summary_path="p",
        risk_summary=[],
        **kw,
    )


def test_answer_defaults_none():
    assert _base().answer is None


def test_answer_accepts_text():
    assert _base(answer="hello").answer == "hello"
