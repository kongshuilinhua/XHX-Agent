from unittest.mock import MagicMock

from xhx_agent.orchestrators.graph import _coordinate


def test_coordinate_parsing():
    ctx = MagicMock()
    ctx.task = "original task"
    client = MagicMock()

    # 1. Normal parsing of "- a\n- b"
    client.chat.return_value = MagicMock(content="- subtask a\n- subtask b")
    res1 = _coordinate(ctx, client)
    assert res1 == ["subtask a", "subtask b"]

    # 2. Fallback to original task when no "- " is present
    client.chat.return_value = MagicMock(content="no subtask prefix")
    res2 = _coordinate(ctx, client)
    assert res2 == ["original task"]

    # 3. Capped to MAX_SUBTASKS (5)
    client.chat.return_value = MagicMock(content="- a\n- b\n- c\n- d\n- e\n- f\n- g")
    res3 = _coordinate(ctx, client)
    assert len(res3) == 5
    assert res3 == ["a", "b", "c", "d", "e"]


def test_graph_worker_commits_changes(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    (tmp_path / "src").mkdir()
    target_file = tmp_path / "src" / "calc.py"
    target_file.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def __init__(self):
            self.w = 0
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="- fix code")
            if "REVIEWER" in system:
                return ChatResult(content="PASS")

            # WORKER
            self.w += 1
            if self.w == 1:
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(id="w1", name="apply_patch", arguments={
                        "patch": "*** Begin Patch\n*** Update File: src/calc.py\n@@\n"
                                 "-    return a + b\n+    return a + b  # edited\n*** End Patch\n"
                    })]
                )
            return ChatResult(content="fixed")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())

    result = RuntimeApp(tmp_path).run_task("refactor", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert "src/calc.py" in result.changed_files
    assert "# edited" in target_file.read_text(encoding="utf-8")


def test_graph_reviewer_retry_then_pass(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class StatefulFake:
        def __init__(self):
            self.review_count = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="- subtask 1")
            if "REVIEWER" in system:
                self.review_count += 1
                if self.review_count == 1:
                    return ChatResult(content="FAIL: needs more comments")
                else:
                    return ChatResult(content="PASS")

            # WORKER
            return ChatResult(content="done subtask")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: StatefulFake())

    result = RuntimeApp(tmp_path).run_task("do task", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert result.turns == 2


def test_graph_reviewer_always_fails(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class AlwaysFailFake:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="- subtask 1")
            if "REVIEWER" in system:
                return ChatResult(content="FAIL: broken test")
            return ChatResult(content="done work")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: AlwaysFailFake())

    result = RuntimeApp(tmp_path).run_task("do task", assume_yes=True, mode="graph")

    assert result.status == "failed"
    assert result.turns == 2
    assert any("FAIL: broken test" in r for r in result.risk_summary)


def test_graph_multiple_subtasks(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class MultiSubtaskFake:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="- task one\n- task two")
            if "REVIEWER" in system:
                return ChatResult(content="PASS")
            # WORKER
            return ChatResult(content="finished subtask")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: MultiSubtaskFake())
    events = []

    result = RuntimeApp(tmp_path).run_task("do multi-task", assume_yes=True, mode="graph", event_callback=events.append)

    assert result.status == "success"
    worker_events = [e for e in events if e.type == "graph_worker"]
    assert len(worker_events) == 2
    assert "Worker on sub-task 1" in worker_events[0].message
    assert "Worker on sub-task 2" in worker_events[1].message
