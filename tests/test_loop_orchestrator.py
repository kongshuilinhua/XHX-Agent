from xhx_agent.runtime.app import RuntimeApp


def test_loop_conversation_returns_answer(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("你是谁，介绍一下", profile_name="mock", mode="loop")
    assert res.status == "success"
    assert res.mode == "loop"
    assert res.answer and "mock" in res.answer.lower()


def test_loop_edit_task_runs_tool_then_answers(tmp_path):
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("fix the bug in README.md", profile_name="mock", mode="loop")
    assert res.status == "success"
    assert res.mode == "loop"
    assert res.answer
