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


def test_loop_malformed_tool_args_does_not_crash(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall

    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={})]),
        ChatResult(content="recovered", tool_calls=[]),
    ]

    class _Fake:
        def __init__(self):
            self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]
            self.i += 1
            return r

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("do something", profile_name="mock", mode="loop")
    assert res.status == "success"
    assert res.answer == "recovered"


def test_loop_denied_unknown_tool_is_fed_back(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall

    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="totally_unknown_tool", arguments={})]),
        ChatResult(content="ok", tool_calls=[]),
    ]

    class _Fake:
        def __init__(self):
            self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]
            self.i += 1
            return r

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("do something", profile_name="mock", mode="loop")
    assert res.status == "success"
    assert res.answer == "ok"


def test_loop_runs_multiple_readonly_tools_in_one_turn(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n", encoding="utf-8")
    seq = [
        ChatResult(content=None, tool_calls=[
            ToolCall(id="c1", name="read_file", arguments={"path": "a.py"}),
            ToolCall(id="c2", name="read_file", arguments={"path": "b.py"})]),
        ChatResult(content="done", tool_calls=[]),
    ]
    class _Fake:
        def __init__(self): self.i = 0
        def chat(self, messages, tools):
            r = seq[self.i]
            self.i += 1
            return r
    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    res = RuntimeApp(tmp_path).run_task("read both", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "done"
