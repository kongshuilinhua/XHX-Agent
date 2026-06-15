from unittest.mock import MagicMock


def test_graph_answers_conversational_directly(tmp_path, monkeypatch):
    """闲聊问题：planner 直接回答，不建图、不启动 execute/synthesize。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class ChatFake:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="answer_user",
                            arguments={"text": "I am xhx-agent. I help you read and change this repo."},
                        )
                    ],
                )
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
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": "n1", "agent_type": "edit", "prompt": "edit calc.py", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "synthesis answer"})]
                )

            # sub-agent
            self.w += 1
            if self.w == 1:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="apply_patch",
                            arguments={
                                "patch": "*** Begin Patch\n*** Update File: src/calc.py\n@@\n"
                                "-    return a + b\n+    return a + b  # edited\n*** End Patch\n"
                            },
                        )
                    ],
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
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "explore", "prompt": "find file", "deps": []},
                                    {
                                        "id": "n2",
                                        "agent_type": "edit",
                                        "prompt": "edit file based on $n1",
                                        "deps": ["n1"],
                                    },
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "synthesis done"})]
                )
            raise AssertionError("Should not call fallback client chat")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())

    explore_called = []

    def fake_run_subagent(ctx, description, prompt, agent_type, turn):
        explore_called.append(prompt)
        return "n1 result text"

    edit_called = []

    def fake_run_write_subagent(ctx, description, prompt, turn, seed_files=None):
        edit_called.append(prompt)
        return "n2 result text", []

    monkeypatch.setattr(graphmod, "run_subagent", fake_run_subagent)
    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    result = RuntimeApp(tmp_path).run_task("refactor dependencies", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert explore_called == ["find file"]
    assert edit_called == ["edit file based on n1 result text"]
    assert result.answer == "synthesis done"


def test_graph_planner_fallback_on_bad_dag(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                # submit_dag 带成环节点 → _nodes_from_args 兜底成单个 edit 节点（prompt=原任务）
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "explore", "prompt": "p1", "deps": ["n2"]},
                                    {"id": "n2", "agent_type": "explore", "prompt": "p2", "deps": ["n1"]},
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "solver finished"})]
                )
            return ChatResult(content="done")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())

    called_prompts = []

    def fake_run_write_subagent(ctx, description, prompt, turn, seed_files=None):
        called_prompts.append(prompt)
        return "fallback success", []

    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    result = RuntimeApp(tmp_path).run_task("my fallback task", assume_yes=True, mode="graph")

    assert result.status == "success"
    assert called_prompts == ["my fallback task"]
    assert result.answer == "solver finished"


def test_nodes_from_args_fallback() -> None:
    from xhx_agent.orchestrators.graph import _nodes_from_args

    # 合法节点
    nodes = _nodes_from_args(
        [
            {"id": "n1", "agent_type": "explore", "prompt": "find", "deps": []},
            {"id": "n2", "agent_type": "edit", "prompt": "edit $n1", "deps": ["n1"]},
        ],
        "fallback task",
    )
    assert [n.node_id for n in nodes] == ["n1", "n2"]
    assert nodes[1].dependencies == ["n1"]

    # 空 → 兜底单 edit
    fb = _nodes_from_args([], "fallback task")
    assert len(fb) == 1 and fb[0].agent_type == "edit" and fb[0].prompt == "fallback task"

    # 悬空 $ref → 兜底
    fb = _nodes_from_args([{"id": "n1", "agent_type": "edit", "prompt": "use $n2", "deps": []}], "fallback task")
    assert len(fb) == 1 and fb[0].prompt == "fallback task"

    # 成环 → 兜底
    fb = _nodes_from_args(
        [
            {"id": "n1", "agent_type": "explore", "prompt": "p1", "deps": ["n2"]},
            {"id": "n2", "agent_type": "explore", "prompt": "p2", "deps": ["n1"]},
        ],
        "fallback task",
    )
    assert len(fb) == 1 and fb[0].prompt == "fallback task"


def test_interpret_plan() -> None:
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.orchestrators.graph import _interpret_plan

    # answer_user → 直答
    r = ChatResult(content=None, tool_calls=[ToolCall(id="a", name="answer_user", arguments={"text": "hi there"})])
    ans, nodes = _interpret_plan(r, "task")
    assert ans == "hi there" and nodes == []

    # submit_dag → DAG
    r = ChatResult(
        content=None,
        tool_calls=[
            ToolCall(
                id="b",
                name="submit_dag",
                arguments={"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "p", "deps": []}]},
            )
        ],
    )
    ans, nodes = _interpret_plan(r, "task")
    assert ans is None and len(nodes) == 1 and nodes[0].node_id == "n1"

    # 没调工具但有纯文本 → 当直答（闲聊兜底）
    r = ChatResult(content="just a plain answer", tool_calls=[])
    ans, nodes = _interpret_plan(r, "task")
    assert ans == "just a plain answer" and nodes == []

    # 没调工具也没文本 → 兜底单 edit
    r = ChatResult(content=None, tool_calls=[])
    ans, nodes = _interpret_plan(r, "the task")
    assert ans is None and len(nodes) == 1 and nodes[0].agent_type == "edit" and nodes[0].prompt == "the task"


def test_plan_function() -> None:
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.orchestrators.graph import _plan

    ctx = MagicMock()
    ctx.task = "some task to plan"
    ctx.scan = MagicMock()
    ctx.original_workspace = MagicMock()

    client = MagicMock()

    # 1. submit_dag → nodes
    client.chat.return_value = ChatResult(
        content=None,
        tool_calls=[
            ToolCall(
                id="p1",
                name="submit_dag",
                arguments={"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "p1", "deps": []}]},
            )
        ],
    )
    ans, nodes = _plan(ctx, client)
    assert ans is None
    assert len(nodes) == 1
    assert nodes[0].node_id == "n1"
    assert nodes[0].prompt == "p1"

    # 2. answer_user → 直答
    client.chat.return_value = ChatResult(
        content=None, tool_calls=[ToolCall(id="p2", name="answer_user", arguments={"text": "Simple Q&A"})]
    )
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

    def fake_run_write_subagent(context, description, prompt, turn, seed_files=None):
        edit_called.append((description, prompt, turn))
        return "edit result", ["src/calc.py"]

    monkeypatch.setattr("xhx_agent.orchestrators.graph.run_write_subagent", fake_run_write_subagent)

    changed, text = _run_dag_node(ctx, node_edit, {"n2": "val2"}, 1)
    assert changed == ["src/calc.py"]
    assert text == "edit result"
    assert edit_called == [("n3", "edit val2", 1)]


def test_graph_runs_independent_explore_nodes_in_parallel(tmp_path, monkeypatch):
    """两个无依赖 explore 节点应并发执行（Barrier 证明；串行则超时凑不齐）。"""
    import threading

    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    barrier = threading.Barrier(2, timeout=5)
    done_prompts: list[str] = []

    def fake_run_subagent(ctx, description, prompt, agent_type, turn):
        barrier.wait()
        done_prompts.append(prompt)
        return f"explored:{prompt}"

    monkeypatch.setattr(graphmod, "run_subagent", fake_run_subagent)

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "explore", "prompt": "look A", "deps": []},
                                    {"id": "n2", "agent_type": "explore", "prompt": "look B", "deps": []},
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "synthesized"})]
                )

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("investigate", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert sorted(done_prompts) == ["look A", "look B"]  # 都越过 barrier == 真并行
    assert result.answer == "synthesized"


def test_graph_node_failure_marks_failed(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    def fake_run_subagent(ctx, description, prompt, agent_type, turn):
        raise ValueError("Simulated explore node failure")

    monkeypatch.setattr(graphmod, "run_subagent", fake_run_subagent)

    class FakeClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "explore", "prompt": "look A", "deps": []},
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "synthesized"})]
                )

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("investigate", assume_yes=True, mode="graph")
    assert result.status == "failed"
    assert any("DAG nodes failed" in r for r in result.risk_summary)


def test_graph_runs_independent_edit_nodes_in_parallel(tmp_path, monkeypatch):
    """两个无依赖 edit 节点应并发执行（Barrier 证明）。"""
    import threading

    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    barrier = threading.Barrier(2, timeout=5)
    done = []

    def fake_run_write_subagent(ctx, description, prompt, turn, seed_files=None):
        barrier.wait()
        done.append(prompt)
        return f"edited:{prompt}", []

    monkeypatch.setattr(graphmod, "run_write_subagent", fake_run_write_subagent)

    class FakeClient:
        def chat(self, messages, tools):
            if "PLANNER" in messages[0]["content"]:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "edit", "prompt": "edit A", "deps": []},
                                    {"id": "n2", "agent_type": "edit", "prompt": "edit B", "deps": []},
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in messages[0]["content"]:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "synthesized"})]
                )

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("two edits", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert sorted(done) == ["edit A", "edit B"]  # 都越过 barrier == 真并行


def test_graph_joiner_replan_then_finish(tmp_path, monkeypatch):
    """round1 joiner→replan；planner 被带反馈二次调用→新节点；round2 joiner→finish。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    explored = []
    monkeypatch.setattr(
        graphmod, "run_subagent", lambda ctx, description, prompt, agent_type, turn: explored.append(prompt) or "r"
    )

    class FakeClient:
        def __init__(self):
            self.plans = 0
            self.joins = 0

        def chat(self, messages, tools):
            s = messages[0]["content"]
            if "PLANNER" in s:
                self.plans += 1
                pid = "a" if self.plans == 1 else "b"
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": pid, "agent_type": "explore", "prompt": f"look{self.plans}", "deps": []}
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in s:
                self.joins += 1
                if self.joins == 1:
                    return ChatResult(
                        content=None, tool_calls=[ToolCall(id="j", name="replan", arguments={"reason": "need more"})]
                    )
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j", name="finish", arguments={"text": "final answer"})]
                )
            raise AssertionError("unexpected")

    fc = FakeClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fc)
    result = RuntimeApp(tmp_path).run_task("t", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert result.answer == "final answer"
    assert fc.plans == 2 and fc.joins == 2  # 重规划了一次
    assert explored == ["look1", "look2"]  # 两轮都执行了


def test_graph_replan_budget_exhausted_forces_finish(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    monkeypatch.setattr(graphmod, "run_subagent", lambda ctx, description, prompt, agent_type, turn: "r")

    class FakeClient:
        def __init__(self):
            self.plans = 0
            self.joins = 0

        def chat(self, messages, tools):
            s = messages[0]["content"]
            if "PLANNER" in s:
                self.plans += 1
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": f"n{self.plans}", "agent_type": "explore", "prompt": "x", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in s:
                self.joins += 1
                names = [t["function"]["name"] for t in tools]
                if "replan" in names:  # 还能 replan 就一直 replan
                    return ChatResult(
                        content=None, tool_calls=[ToolCall(id="j", name="replan", arguments={"reason": "again"})]
                    )
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j", name="finish", arguments={"text": "forced finish"})]
                )
            raise AssertionError

    fc = FakeClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fc)
    result = RuntimeApp(tmp_path).run_task("t", assume_yes=True, mode="graph")
    assert result.answer == "forced finish"
    assert fc.plans == 3  # 默认 max_graph_replans=2 → 1 初规划 + 2 重规划
    assert fc.joins == 3


def test_graph_replan_reedits_same_file_across_rounds(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    # 1. Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    init_file = tmp_path / "init.txt"
    init_file.write_text("initial commit file", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # 2. Graph Fake Client
    class FakeGraphClient:
        def __init__(self):
            self.plans = 0
            self.joins = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                self.plans += 1
                node_id = f"n{self.plans}"
                prompt = f"edit foo.py to add line{self.plans}"
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=f"p{self.plans}",
                            name="submit_dag",
                            arguments={"nodes": [{"id": node_id, "agent_type": "edit", "prompt": prompt, "deps": []}]},
                        )
                    ],
                )
            if "JOINER" in system:
                self.joins += 1
                if self.joins == 1:
                    return ChatResult(
                        content=None,
                        tool_calls=[ToolCall(id="j1", name="replan", arguments={"reason": "need line2 as well"})],
                    )
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j2", name="finish", arguments={"text": "final done"})]
                )
            raise AssertionError(f"Unexpected graph message: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # 3. Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def __init__(self):
            self.turn_n1 = 0
            self.turn_n2 = 0

        def chat(self, messages, tools):
            # The initial user prompt is the first message in the history with role == "user"
            user_msg = next(m["content"] for m in messages if m["role"] == "user")
            if "add line1" in user_msg:
                self.turn_n1 += 1
                if self.turn_n1 == 1:
                    patch = textwrap.dedent("""\
                        --- /dev/null
                        +++ b/foo.py
                        @@ -0,0 +1,1 @@
                        +line1
                        """)
                    return ChatResult(
                        content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
                    )
                return ChatResult(content="Done line1", tool_calls=[])
            elif "add line2" in user_msg:
                self.turn_n2 += 1
                if self.turn_n2 == 1:
                    patch = textwrap.dedent("""\
                        --- a/foo.py
                        +++ b/foo.py
                        @@ -1,1 +1,2 @@
                         line1
                        +line2
                        """)
                    return ChatResult(
                        content=None, tool_calls=[ToolCall(id="c2", name="apply_patch", arguments={"patch": patch})]
                    )
                return ChatResult(content="Done line2", tool_calls=[])
            raise AssertionError(f"Unexpected subagent prompt: {user_msg}")

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    # 4. Run task
    result = RuntimeApp(tmp_path).run_task("replan and add line1 and line2 to foo.py", assume_yes=True, mode="graph")

    # 5. Assertions
    assert result.status == "success"
    assert result.answer == "final done"
    assert "foo.py" in result.changed_files

    foo_file = tmp_path / "foo.py"
    assert foo_file.exists()
    assert foo_file.read_text(encoding="utf-8") == "line1\nline2\n"


def test_graph_runs_real_verification_on_changes(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.verification.router import VerificationCommand, VerificationPlan

    # 1. Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    init_file = tmp_path / "init.txt"
    init_file.write_text("initial commit file", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # 2. Graph Fake Client
    class FakeGraphClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": "n1", "agent_type": "edit", "prompt": "edit file", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: passed" in summary
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # 3. Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def chat(self, messages, tools):
            user_msg = next(m["content"] for m in messages if m["role"] == "user")
            if "edit file" in user_msg:
                patch = textwrap.dedent("""\
                    --- a/init.txt
                    +++ b/init.txt
                    @@ -1,1 +1,2 @@
                     initial commit file
                    +new change
                    """)
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
                )
            return ChatResult(content="Done", tool_calls=[])

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    # Mock infer_verification to run a stable, fast Python process that exits with 0
    dummy_cmd = "python -m pytest --version"

    def mock_infer_verification(workspace, changed_files=None):
        return VerificationPlan(commands=[VerificationCommand(command=dummy_cmd, reason="test mock")])

    monkeypatch.setattr("xhx_agent.verification.router.infer_verification", mock_infer_verification)

    result = RuntimeApp(tmp_path).run_task("do a change", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert result.verification == "passed"
    assert dummy_cmd in result.commands
    assert len(result.verification_results) == 1
    assert result.verification_results[0].status == "success"


def test_graph_verification_skipped_when_no_changes(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    # Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    init_file = tmp_path / "init.txt"
    init_file.write_text("initial commit file", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # Graph Fake Client (explore-only)
    class FakeGraphClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": "n1", "agent_type": "explore", "prompt": "just explore", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: skipped_no_changes" in summary
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # Subagent Fake Client
    import xhx_agent.orchestrators.subagent as subagentmod

    class FakeChildClient:
        def chat(self, messages, tools):
            return ChatResult(content="Explored", tool_calls=[])

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    result = RuntimeApp(tmp_path).run_task("do explore", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert result.verification == "skipped_no_changes"
    assert result.commands == []


def test_graph_verification_failed_surfaces(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.verification.router import VerificationCommand, VerificationPlan

    # Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    init_file = tmp_path / "init.txt"
    init_file.write_text("initial commit file", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # Graph Fake Client
    class FakeGraphClient:
        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": "n1", "agent_type": "edit", "prompt": "edit file", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: failed" in summary
                assert "Verification failure output:" in summary
                assert any(x in summary.lower() for x in ["unrecognized", "usage", "option", "invalid"])
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "failed but done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def chat(self, messages, tools):
            user_msg = next(m["content"] for m in messages if m["role"] == "user")
            if "edit file" in user_msg:
                patch = textwrap.dedent("""\
                    --- a/init.txt
                    +++ b/init.txt
                    @@ -1,1 +1,2 @@
                     initial commit file
                    +new change
                    """)
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
                )
            return ChatResult(content="Done", tool_calls=[])

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    # Mock infer_verification to run a failing Python process
    dummy_cmd = "python -m pytest --invalid-option-xyz"

    def mock_infer_verification(workspace, changed_files=None):
        return VerificationPlan(commands=[VerificationCommand(command=dummy_cmd, reason="test mock fail")])

    monkeypatch.setattr("xhx_agent.verification.router.infer_verification", mock_infer_verification)

    result = RuntimeApp(tmp_path).run_task("do a change", assume_yes=True, mode="graph")
    # status is success because DAG nodes ran successfully, even though verification failed
    assert result.status == "success"
    assert result.verification == "failed"
    assert dummy_cmd in result.commands
    assert len(result.verification_results) == 1
    assert result.verification_results[0].status == "failed"


def test_graph_repairs_on_verification_failure(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    # Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    # Place a target file and a test file
    calc_file = tmp_path / "calc.py"
    calc_file.write_text("def add(a, b):\n    pass\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_calc.py"
    test_file.write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")

    subprocess.run(["git", "add", "calc.py", "tests/test_calc.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # Graph Fake Client
    class FakeGraphClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                self.calls += 1
                prompt = "edit calc.py incorrectly" if self.calls == 1 else "edit calc.py correctly"
                node_id = f"n{self.calls}"
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=f"p{self.calls}",
                            name="submit_dag",
                            arguments={"nodes": [{"id": node_id, "agent_type": "edit", "prompt": prompt, "deps": []}]},
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: passed" in summary
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "final done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def chat(self, messages, tools):
            user_msg = next(m["content"] for m in messages if m["role"] == "user")
            if "incorrect" in user_msg:
                patch = textwrap.dedent("""\
                    --- a/calc.py
                    +++ b/calc.py
                    @@ -1,2 +1,2 @@
                     def add(a, b):
                    -    pass
                    +    return a - b
                    """)
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
                )
            elif "correct" in user_msg:
                patch = textwrap.dedent("""\
                    --- a/calc.py
                    +++ b/calc.py
                    @@ -1,2 +1,2 @@
                     def add(a, b):
                    -    return a - b
                    +    return a + b
                    """)
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="c2", name="apply_patch", arguments={"patch": patch})]
                )
            return ChatResult(content="Done", tool_calls=[])

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    result = RuntimeApp(tmp_path).run_task("fix calc", assume_yes=True, auto_repair=True, mode="graph")
    assert result.status == "success"
    assert result.verification == "passed"
    assert fgc.calls == 2
    assert result.repair_attempts == 1
    assert "calc.py" in result.changed_files
    assert calc_file.read_text(encoding="utf-8") == "def add(a, b):\n    return a + b\n"


def test_graph_no_repair_when_auto_repair_off(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    # Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    calc_file = tmp_path / "calc.py"
    calc_file.write_text("def add(a, b):\n    pass\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_calc.py"
    test_file.write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")

    subprocess.run(["git", "add", "calc.py", "tests/test_calc.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # Graph Fake Client
    class FakeGraphClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                self.calls += 1
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {"id": "n1", "agent_type": "edit", "prompt": "edit calc.py incorrectly", "deps": []}
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: failed" in summary
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "failed but done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def chat(self, messages, tools):
            user_msg = next(m["content"] for m in messages if m["role"] == "user")
            if "incorrect" in user_msg:
                patch = textwrap.dedent("""\
                    --- a/calc.py
                    +++ b/calc.py
                    @@ -1,2 +1,2 @@
                     def add(a, b):
                    -    pass
                    +    return a - b
                    """)
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
                )
            return ChatResult(content="Done", tool_calls=[])

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    # auto_repair is False by default
    result = RuntimeApp(tmp_path).run_task("fix calc", assume_yes=True, auto_repair=False, mode="graph")
    assert result.status == "success"
    assert result.verification == "failed"
    assert fgc.calls == 1
    assert result.repair_attempts == 0
    assert result.restore_plan_path is not None


def test_graph_repair_budget_exhausted(tmp_path, monkeypatch):
    import subprocess

    import xhx_agent.orchestrators.graph as graphmod
    import xhx_agent.orchestrators.subagent as subagentmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    # Setup git repository
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    calc_file = tmp_path / "calc.py"
    calc_file.write_text("def add(a, b):\n    pass\n", encoding="utf-8")

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    test_file = tests_dir / "test_calc.py"
    test_file.write_text("from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")

    subprocess.run(["git", "add", "calc.py", "tests/test_calc.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    # Graph Fake Client
    class FakeGraphClient:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "PLANNER" in system:
                self.calls += 1
                node_id = f"n{self.calls}"
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id=f"p{self.calls}",
                            name="submit_dag",
                            arguments={
                                "nodes": [
                                    {
                                        "id": node_id,
                                        "agent_type": "edit",
                                        "prompt": "edit calc.py incorrectly",
                                        "deps": [],
                                    }
                                ]
                            },
                        )
                    ],
                )
            if "JOINER" in system:
                summary = next(m["content"] for m in messages if m["role"] == "user")
                assert "Verification result: failed" in summary
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "failed but done"})]
                )
            raise AssertionError(f"Unexpected: {messages}")

    fgc = FakeGraphClient()
    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: fgc)

    # Subagent Fake Client
    import textwrap

    class FakeChildClient:
        def chat(self, messages, tools):
            # Always return a broken implementation
            patch = textwrap.dedent("""\
                --- a/calc.py
                +++ b/calc.py
                @@ -1,2 +1,2 @@
                 def add(a, b):
                -    pass
                +    return a - b - 1
                """)
            return ChatResult(
                content=None, tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={"patch": patch})]
            )

    fcc = FakeChildClient()
    monkeypatch.setattr(subagentmod, "build_chat_client", lambda profile: fcc)

    # max_graph_replans is 2 by default. So 1 initial planning + 2 repair attempts.
    result = RuntimeApp(tmp_path).run_task("fix calc", assume_yes=True, auto_repair=True, mode="graph")
    assert result.status == "success"
    assert result.verification == "failed"
    # 1 initial planner call + 2 repair planner calls = 3 total calls
    assert fgc.calls == 3
    assert result.repair_attempts == 2
    assert result.restore_plan_path is not None


def test_graph_joiner_summary_lists_changed_files(tmp_path, monkeypatch):
    """防假成功：joiner 的 summary 必须含真实改动文件清单（edit 改了 README.md → 列出它）。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    monkeypatch.setattr(
        graphmod,
        "run_write_subagent",
        lambda ctx, description, prompt, turn, seed_files=None: ("edited", ["README.md"]),
    )

    seen = {}

    class FakeClient:
        def chat(self, messages, tools):
            s = messages[0]["content"]
            if "PLANNER" in s:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={
                                "nodes": [{"id": "n1", "agent_type": "edit", "prompt": "edit readme", "deps": []}]
                            },
                        )
                    ],
                )
            if "JOINER" in s:
                seen["summary"] = next(m["content"] for m in messages if m["role"] == "user")
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "done"})]
                )
            raise AssertionError("unexpected")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("edit the readme", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert "Files actually changed this run: ['README.md']" in seen["summary"]


def test_graph_joiner_summary_marks_no_changes(tmp_path, monkeypatch):
    """防假成功：explore-only(无改动)时 joiner summary 标 NONE，杜绝它谎称改过文件。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    monkeypatch.setattr(
        graphmod, "run_subagent", lambda ctx, description, prompt, agent_type, turn: "explored, suggest changing X"
    )

    seen = {}

    class FakeClient:
        def chat(self, messages, tools):
            s = messages[0]["content"]
            if "PLANNER" in s:
                return ChatResult(
                    content=None,
                    tool_calls=[
                        ToolCall(
                            id="p1",
                            name="submit_dag",
                            arguments={"nodes": [{"id": "n1", "agent_type": "explore", "prompt": "look", "deps": []}]},
                        )
                    ],
                )
            if "JOINER" in s:
                seen["summary"] = next(m["content"] for m in messages if m["role"] == "user")
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="j1", name="finish", arguments={"text": "investigated"})]
                )
            raise AssertionError("unexpected")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: FakeClient())
    result = RuntimeApp(tmp_path).run_task("look around", assume_yes=True, mode="graph")
    assert result.status == "success"
    assert "Files actually changed this run: NONE" in seen["summary"]


def test_graph_planner_emits_progress_before_llm_call(tmp_path, monkeypatch):
    """planner 调用前先发进度事件，避免界面在 LLM 调用期间(最长 60s)定格成卡死。"""
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()

    class ChatFake:
        def chat(self, messages, tools):
            if "PLANNER" in messages[0]["content"]:
                return ChatResult(
                    content=None, tool_calls=[ToolCall(id="p1", name="answer_user", arguments={"text": "hi"})]
                )
            raise AssertionError("only planner should run for a chat request")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: ChatFake())
    events = []
    RuntimeApp(tmp_path).run_task("你好", assume_yes=True, mode="graph", event_callback=events.append)

    planner_msgs = [e.message for e in events if getattr(e, "type", "") == "graph_planner"]
    assert "Planning the task…" in planner_msgs  # 调用前进度
    assert any("Answered directly" in m for m in planner_msgs)  # 调用后结果
    # 进度在结果之前 → 用户在 LLM 调用期间就能看到活动
    assert planner_msgs.index("Planning the task…") < next(
        i for i, m in enumerate(planner_msgs) if "Answered directly" in m
    )


def test_graph_planner_sees_prior_messages(tmp_path, monkeypatch):
    """跨轮记忆：graph 的 planner 必须看到上一轮历史，否则追问时失忆。

    回归 graph 模式下「明确需求后再追问『做好了吗?』，planner 却说不知道要做什么」的 bug——
    根因是 _plan 只喂当前 task、不接 prior_messages（loop/plan 早已接，graph 漏了）。
    """
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    seen: dict = {}

    class ChatFake:
        def chat(self, messages, tools):
            seen["roles"] = [m["role"] for m in messages]
            seen["contents"] = [m.get("content") for m in messages]
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="p1", name="answer_user", arguments={"text": "ok"})],
            )

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: ChatFake())
    prior = [
        {"role": "system", "content": "OLD SYSTEM — must be dropped"},
        {"role": "user", "content": "做一个迷宫游戏 Flask+Canvas"},
        {"role": "assistant", "content": "已创建 app.py 和 index.html"},
    ]
    result = RuntimeApp(tmp_path).run_task(
        "做好了吗?", assume_yes=True, mode="graph", prior_messages=prior
    )

    assert result.status == "success"
    # 恰好一个 system（新的），旧 system 被丢弃；历史 user/assistant 接上；新 task 在末尾
    assert seen["roles"].count("system") == 1
    assert "OLD SYSTEM — must be dropped" not in seen["contents"]
    assert "做一个迷宫游戏 Flask+Canvas" in seen["contents"]
    assert seen["roles"][-1] == "user" and seen["contents"][-1] == "做好了吗?"
