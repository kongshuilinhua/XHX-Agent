from unittest.mock import MagicMock


def test_graph_answers_conversational_directly(tmp_path, monkeypatch):
    """闲聊问题：planner 直接回答，不建图、不启动 execute/synthesize。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class ChatFake:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(content="ANSWER: I am xhx-agent. I help you read and change this repo.")
            raise AssertionError("execute/synthesize should not be called for a conversational request")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: ChatFake())
    events = []

    result = RuntimeApp(tmp_path).run_task(
        "介绍一下你自己", assume_yes=True, mode="graph", event_callback=events.append
    )

    assert result.status == "success"
    assert result.changed_files == []
    assert result.answer == "I am xhx-agent. I help you read and change this repo."
    # 没有任何 graph_node 事件
    assert not [e for e in events if e.type == "graph_node"]


def test_graph_single_edit_node_changes_code(tmp_path, monkeypatch):
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
            if "PLANNER" in system:
                return ChatResult(
                    content='{"nodes": [{"id": "n1", "agent_type": "edit", "prompt": "edit calc.py", "deps": []}]}'
                )
            if "SOLVER" in system:
                return ChatResult(content="synthesis answer")

            # sub-agent
            self.w += 1
            if self.w == 1:
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(id="w1", name="apply_patch", arguments={
                        "patch": "*** Begin Patch\n*** Update File: src/calc.py\n@@\n"
                                 "-    return a + b\n+    return a + b  # edited\n*** End Patch\n"
                    })]
                )
            return ChatResult(content="done editing")

    import xhx_agent.orchestrators.subagent as subagentmod
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: FakeClient())

    result = RuntimeApp(tmp_path).run_task("refactor", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert "src/calc.py" in result.changed_files
    assert "# edited" in target_file.read_text(encoding="utf-8")
    assert result.answer == "synthesis answer"


def test_graph_runs_dependent_nodes_with_variable_substitution(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content='{"nodes": ['
                            '{"id": "n1", "agent_type": "explore", "prompt": "find file", "deps": []},'
                            '{"id": "n2", "agent_type": "edit", "prompt": "edit file based on $n1", "deps": ["n1"]}'
                            ']}'
                )
            if "SOLVER" in system:
                return ChatResult(content="synthesis done")
            raise AssertionError("Should not call fallback client chat")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())

    explore_called = []
    def fake_run_subagent(ctx, description, prompt, agent_type, turn):
        explore_called.append(prompt)
        return "n1 result text"

    edit_called = []
    def fake_run_write_subagent(ctx, description, prompt, turn):
        edit_called.append(prompt)
        return "n2 result text", []

    monkeypatch.setattr(graphmod, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    result = RuntimeApp(tmp_path).run_task("refactor dependencies", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert explore_called == ["find file"]
    assert edit_called == ["edit file based on n1 result text"]
    assert result.answer == "synthesis done"


def test_graph_planner_fallback_on_bad_json(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(content="invalid json here {{{")
            if "SOLVER" in system:
                return ChatResult(content="solver finished")
            return ChatResult(content="done")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())

    called_prompts = []
    def fake_run_write_subagent(ctx, description, prompt, turn):
        called_prompts.append(prompt)
        return "fallback success", []

    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    result = RuntimeApp(tmp_path).run_task("my fallback task", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert called_prompts == ["my fallback task"]
    assert result.answer == "solver finished"


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
    from xhx_agent.models.types import ChatResult
    from xhx_agent.orchestrators.graph import _plan

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
    from xhx_agent.orchestrators.graph import _run_dag_node, _substitute_vars
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



