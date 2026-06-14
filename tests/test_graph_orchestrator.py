from unittest.mock import MagicMock

from xhx_agent.orchestrators.graph import _coordinate


def test_coordinate_parsing():
    ctx = MagicMock()
    ctx.task = "original task"
    client = MagicMock()

    # 1. Normal parsing of "- a\n- b"
    client.chat.return_value = MagicMock(content="- subtask a\n- subtask b")
    res1 = _coordinate(ctx, client)
    assert res1.answer is None
    assert res1.subtasks == ["subtask a", "subtask b"]

    # 2. Fallback to original task when no "- " is present
    client.chat.return_value = MagicMock(content="no subtask prefix")
    res2 = _coordinate(ctx, client)
    assert res2.answer is None
    assert res2.subtasks == ["original task"]

    # 3. Capped to MAX_SUBTASKS (5)
    client.chat.return_value = MagicMock(content="- a\n- b\n- c\n- d\n- e\n- f\n- g")
    res3 = _coordinate(ctx, client)
    assert res3.answer is None
    assert len(res3.subtasks) == 5
    assert res3.subtasks == ["a", "b", "c", "d", "e"]

    # 4. Conversational request → direct answer, no sub-tasks (量级匹配出口)
    client.chat.return_value = MagicMock(content="ANSWER: I am xhx-agent, a local coding agent.")
    res4 = _coordinate(ctx, client)
    assert res4.subtasks == []
    assert res4.answer == "I am xhx-agent, a local coding agent."


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


def test_graph_answers_conversational_directly(tmp_path, monkeypatch):
    """闲聊问题：coordinator 直接回答，不拆任务、不启动 worker、不跑 review。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class ChatFake:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="ANSWER: I am xhx-agent. I help you read and change this repo.")
            raise AssertionError("worker/reviewer should not be called for a conversational request")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: ChatFake())
    events = []

    result = RuntimeApp(tmp_path).run_task(
        "介绍一下你自己", assume_yes=True, mode="graph", event_callback=events.append
    )

    assert result.status == "success"
    assert result.changed_files == []
    assert result.answer == "I am xhx-agent. I help you read and change this repo."
    # 没有任何 worker 子任务事件
    assert not [e for e in events if e.type == "graph_worker"]


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


def test_parse_dag_robustness() -> None:
    from xhx_agent.orchestrators.graph import _parse_dag

    # 1. 闲聊 -> answer
    ans, nodes = _parse_dag("ANSWER: Hello there!", "fallback task")
    assert ans == "Hello there!"
    assert nodes == []

    # 1.1 闲聊大小写不敏感或有空格
    ans, nodes = _parse_dag("answer:  How can I help?  ", "fallback task")
    assert ans == "How can I help?"
    assert nodes == []

    # 2. 合法 JSON -> nodes
    raw_json = '{"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "find file", "deps": []}, {"id": "n2", "agent_type": "edit", "prompt": "edit file $n1", "deps": ["n1"]}]}'
    ans, nodes = _parse_dag(raw_json, "fallback task")
    assert ans is None
    assert len(nodes) == 2
    assert nodes[0].node_id == "n1"
    assert nodes[0].agent_type == "explore"
    assert nodes[0].prompt == "find file"
    assert nodes[0].dependencies == []
    assert nodes[1].node_id == "n2"
    assert nodes[1].agent_type == "edit"
    assert nodes[1].prompt == "edit file $n1"
    assert nodes[1].dependencies == ["n1"]

    # 3. 带 ```json 围栏仍解析
    fenced = "Some thinking here...\n```json\n" + raw_json + "\n```\nSome other tail..."
    ans, nodes = _parse_dag(fenced, "fallback task")
    assert ans is None
    assert len(nodes) == 2

    # 4. 非法 JSON -> 兜底单 edit 节点
    ans, nodes = _parse_dag("{invalid json", "fallback task")
    assert ans is None
    assert len(nodes) == 1
    assert nodes[0].node_id == "n1"
    assert nodes[0].agent_type == "edit"
    assert nodes[0].prompt == "fallback task"

    # 5. $ref 不在 deps -> 兜底
    bad_ref = '{"nodes": [{"id": "n1", "agent_type": "edit", "prompt": "use $n2", "deps": []}]}'
    ans, nodes = _parse_dag(bad_ref, "fallback task")
    assert ans is None
    assert len(nodes) == 1
    assert nodes[0].node_id == "n1"
    assert nodes[0].agent_type == "edit"
    assert nodes[0].prompt == "fallback task"

    # 6. 成环 -> 兜底
    cyclic = '{"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "p1", "deps": ["n2"]}, {"id": "n2", "agent_type": "explore", "prompt": "p2", "deps": ["n1"]}]}'
    ans, nodes = _parse_dag(cyclic, "fallback task")
    assert ans is None
    assert len(nodes) == 1
    assert nodes[0].node_id == "n1"
    assert nodes[0].agent_type == "edit"
    assert nodes[0].prompt == "fallback task"


def test_plan_function() -> None:
    from unittest.mock import MagicMock
    from xhx_agent.orchestrators.graph import _plan
    from xhx_agent.models.types import ChatResult

    ctx = MagicMock()
    ctx.task = "some task to plan"
    ctx.scan = MagicMock()
    ctx.original_workspace = MagicMock()

    client = MagicMock()

    # 1. 正常返回 JSON
    client.chat.return_value = ChatResult(
        content='{"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "p1", "deps": []}]}'
    )
    ans, nodes = _plan(ctx, client)
    assert ans is None
    assert len(nodes) == 1
    assert nodes[0].node_id == "n1"
    assert nodes[0].prompt == "p1"

    # 2. 返回闲聊
    client.chat.return_value = ChatResult(content="ANSWER: Simple Q&A")
    ans, nodes = _plan(ctx, client)
    assert ans == "Simple Q&A"
    assert len(nodes) == 0


def test_variable_substitution_and_node_execution(monkeypatch) -> None:
    from xhx_agent.orchestrators.graph import _substitute_vars, _run_dag_node
    from xhx_agent.planner.modes import DAGNode

    # 1. 变量替换测试
    done = {"n1": "value1", "n2": "value2"}
    assert _substitute_vars("use $n1 and $n2", done) == "use value1 and value2"
    assert _substitute_vars("use $n1 and $unknown", done) == "use value1 and $unknown"

    # 2. _run_dag_node explore 测试
    ctx = "fake_ctx"
    node_explore = DAGNode(node_id="n1", prompt="explore $n2", agent_type="explore")

    explore_called = []
    def fake_run_subagent(context, description, prompt, agent_type, turn):
        explore_called.append((description, prompt, agent_type, turn))
        return "explore result"

    monkeypatch.setattr("xhx_agent.orchestrators.graph.run_subagent", fake_run_subagent)

    changed, text = _run_dag_node(ctx, node_explore, {"n2": "val2"}, 1)
    assert changed == []
    assert text == "explore result"
    assert explore_called == [("n1", "explore val2", "explore", 1)]

    # 3. _run_dag_node edit 测试
    node_edit = DAGNode(node_id="n3", prompt="edit $n2", agent_type="edit")

    edit_called = []
    def fake_run_write_subagent(context, description, prompt, turn):
        edit_called.append((description, prompt, turn))
        return "edit result", ["src/calc.py"]

    monkeypatch.setattr("xhx_agent.orchestrators.graph.run_write_subagent", fake_run_write_subagent)

    changed, text = _run_dag_node(ctx, node_edit, {"n2": "val2"}, 1)
    assert changed == ["src/calc.py"]
    assert text == "edit result"
    assert edit_called == [("n3", "edit val2", 1)]



