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
        ChatResult(
            content=None,
            tool_calls=[
                ToolCall(id="c1", name="read_file", arguments={"path": "a.py"}),
                ToolCall(id="c2", name="read_file", arguments={"path": "b.py"}),
            ],
        ),
        ChatResult(content="done", tool_calls=[]),
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
    res = RuntimeApp(tmp_path).run_task("read both", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "done"


def test_loop_terminal_tool_runs_safe_command(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall

    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="terminal", arguments={"command": "git status"})]),
        ChatResult(content="checked", tool_calls=[]),
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
    events = []
    res = RuntimeApp(tmp_path).run_task("check status", profile_name="mock", mode="loop", event_callback=events.append)
    assert res.status == "success" and res.answer == "checked"
    # The command must have actually run through the command-level safety gate.
    assert any(e.type == "policy_decision" and e.payload.get("command") == "git status" for e in events)


def test_loop_terminal_deny_is_fed_back(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall

    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="terminal", arguments={"command": "rm -rf src"})]),
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
    res = RuntimeApp(tmp_path).run_task("delete", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "ok"


def test_loop_persists_full_transcript(tmp_path, monkeypatch):
    import json

    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    seq = [
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "README.md"})]),
        ChatResult(content="done reading", tool_calls=[]),
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
    res = RuntimeApp(tmp_path).run_task("read it", profile_name="mock", mode="loop")
    assert res.status == "success" and res.answer == "done reading"
    assert res.transcript_path is not None
    saved = json.loads((tmp_path / res.transcript_path).read_text(encoding="utf-8"))
    roles = [m["role"] for m in saved]
    assert roles[0] == "system" and "user" in roles and "tool" in roles
    # 最终 assistant 回答必须在历史里（修复"漏存最后一句"）
    assert saved[-1] == {"role": "assistant", "content": "done reading"}


def test_loop_restores_prior_messages(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    seen = {}

    class _Fake:
        def chat(self, messages, tools):
            seen["roles"] = [m["role"] for m in messages]
            seen["contents"] = [m.get("content") for m in messages]
            return ChatResult(content="continued", tool_calls=[])

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: _Fake())
    RuntimeApp(tmp_path).init_project()
    prior = [
        {"role": "system", "content": "OLD SYSTEM — must be dropped"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
    ]
    res = RuntimeApp(tmp_path).run_task("follow up", profile_name="mock", mode="loop", prior_messages=prior)
    assert res.status == "success" and res.answer == "continued"
    # 恰好一个 system（新的），旧 system 被丢弃；历史 user/assistant 在；新 task 在末尾
    assert seen["roles"].count("system") == 1
    assert "OLD SYSTEM — must be dropped" not in seen["contents"]
    assert "earlier question" in seen["contents"]
    assert seen["roles"][-1] == "user" and seen["contents"][-1] == "follow up"


def test_loop_runs_explore_dispatch_batch_in_parallel(tmp_path, monkeypatch):
    """一轮里多个 explore dispatch 应并发执行。

    用 Barrier(2) 证明并行：两个子 agent 必须同时到达 barrier 才能越过；
    若串行执行，第一个会一直等到 5s 超时触发 BrokenBarrierError，两个都越不过，completed 为空。
    """
    import threading

    import xhx_agent.orchestrators.loop as loopmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    barrier = threading.Barrier(2, timeout=5)
    completed: list[str] = []

    def fake_run_subagent(ctx, *, description, prompt, agent_type="explore", turn=0):
        barrier.wait()  # 仅当两个线程同时到达才返回；串行 → 超时 BrokenBarrierError
        completed.append(prompt)
        return f"[sub-agent explore] {prompt}"

    monkeypatch.setattr(subagentmod, "run_subagent", fake_run_subagent)

    class FakeClient:
        def __init__(self) -> None:
            self.n = 0

        def chat(self, messages, tools):
            self.n += 1
            if self.n == 1:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(id="d1", name="dispatch", arguments={"prompt": "explore A", "agent_type": "explore"}),
                        ToolCall(id="d2", name="dispatch", arguments={"prompt": "explore B", "agent_type": "explore"}),
                    ],
                )
            return ChatResult(content="done")

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeClient())

    result = RuntimeApp(tmp_path).run_task("investigate two modules", assume_yes=True, mode="loop")

    assert result.status == "success"
    # 两个 explore 都越过 barrier == 真并行；串行时 completed 为空、断言失败
    assert sorted(completed) == ["explore A", "explore B"]
