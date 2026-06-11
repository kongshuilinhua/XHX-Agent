from unittest.mock import MagicMock

from xhx_agent.models.types import ChatResult, ToolCall
from xhx_agent.orchestrators.subagent import run_subagent
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.tools.registry import default_tool_registry


def test_dispatch_schema_exposure():
    registry = default_tool_registry()
    schemas = registry.tool_schemas()
    dispatch_schema = next((s for s in schemas if s["function"]["name"] == "dispatch"), None)
    assert dispatch_schema is not None
    assert "prompt" in dispatch_schema["function"]["parameters"]["required"]
    assert "description" in dispatch_schema["function"]["parameters"]["properties"]
    assert "agent_type" in dispatch_schema["function"]["parameters"]["properties"]


def test_subagent_e2e_loop(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    import xhx_agent.orchestrators.subagent as submod

    # Setup dummy project files
    (tmp_path / "README.md").write_text("Hello World", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    parent_results = [
        # Turn 0: dispatch
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="p1", name="dispatch", arguments={
                "description": "explore readme content",
                "prompt": "Read the readme file and tell me what is in it",
                "agent_type": "explore"
            })]
        ),
        # Turn 1: final answer
        ChatResult(content="Parent done", tool_calls=[])
    ]

    seen_parent_messages = []

    class FakeParentClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            seen_parent_messages.clear()
            seen_parent_messages.extend(messages)
            r = parent_results[self.i]
            self.i += 1
            return r

    child_results = [
        # Turn 0: read_file
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "README.md"})]
        ),
        # Turn 1: conclusion
        ChatResult(content="Child conclusion: README.md says Hello World", tool_calls=[])
    ]

    class FakeChildClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            r = child_results[self.i]
            self.i += 1
            return r

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeParentClient())
    monkeypatch.setattr(submod, "build_chat_client", lambda profile: FakeChildClient())

    res = RuntimeApp(tmp_path).run_task("do subagent task", profile_name="mock", mode="loop")

    assert res.status == "success"
    assert res.answer == "Parent done"

    # Verify that the parent received the subagent conclusion
    tool_msg = next((m for m in seen_parent_messages if m.get("role") == "tool" and m.get("tool_call_id") == "p1"), None)
    assert tool_msg is not None
    assert "[sub-agent explore]" in tool_msg["content"]
    assert "Child conclusion: README.md says Hello World" in tool_msg["content"]


def test_subagent_explore_denies_patch(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    import xhx_agent.orchestrators.subagent as submod

    # Setup dummy project files
    target_file = tmp_path / "target.py"
    target_file.write_text("original = 1\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    parent_results = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="p1", name="dispatch", arguments={
                "description": "try update",
                "prompt": "try update",
                "agent_type": "explore"
            })]
        ),
        ChatResult(content="Parent done", tool_calls=[])
    ]

    class FakeParentClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            r = parent_results[self.i]
            self.i += 1
            return r

    child_results = [
        # Try to call apply_patch (not allowed)
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={
                "patch": "*** Begin Patch\n*** Update File: target.py\n@@\n-original = 1\n+original = 2\n*** End Patch\n"
            })]
        ),
        ChatResult(content="Child concluded", tool_calls=[])
    ]

    seen_child_messages = []

    class FakeChildClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            seen_child_messages.clear()
            seen_child_messages.extend(messages)
            r = child_results[self.i]
            self.i += 1
            return r

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeParentClient())
    monkeypatch.setattr(submod, "build_chat_client", lambda profile: FakeChildClient())

    res = RuntimeApp(tmp_path).run_task("do subagent task", profile_name="mock", mode="loop")

    assert res.status == "success"
    # Ensure apply_patch tool result indicates it was not allowed
    tool_msgs = [m for m in seen_child_messages if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "not allowed" in tool_msgs[0]["content"]
    assert "apply_patch" in tool_msgs[0]["content"]

    # Ensure target file remains unchanged
    assert target_file.read_text(encoding="utf-8") == "original = 1\n"


def test_subagent_unknown_agent_type():
    ctx = MagicMock()
    res = run_subagent(ctx, description="test", prompt="test", agent_type="writer")
    assert "unknown agent_type" in res
    assert "writer" in res


def test_subagent_turn_limit(monkeypatch):
    import xhx_agent.orchestrators._toolturn as toolturnmod
    import xhx_agent.orchestrators.subagent as submod

    class InfiniteToolClient:
        def chat(self, messages, tools):
            return ChatResult(
                content=None,
                tool_calls=[ToolCall(id="t1", name="search", arguments={"query": "infinite"})]
            )

    monkeypatch.setattr(submod, "build_chat_client", lambda profile: InfiniteToolClient())
    monkeypatch.setattr(
        toolturnmod, "_execute_tool_call_rich",
        lambda ctx, tc, turn: (tc, "infinite result", [], None)
    )

    ctx = MagicMock()
    ctx.kernel.tool_registry.tool_schemas.return_value = [
        {"function": {"name": "search", "parameters": {}}}
    ]

    res = run_subagent(ctx, description="infinite test", prompt="search forever", agent_type="explore")

    assert "reached its turn limit" in res
    assert "explore" in res


def test_subagent_plan_supports_dispatch(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.plan as planmod
    import xhx_agent.orchestrators.subagent as submod

    (tmp_path / "README.md").write_text("Plan Hello", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    parent_results = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="p1", name="dispatch", arguments={
                "description": "plan explore",
                "prompt": "read README.md",
                "agent_type": "explore"
            })]
        ),
        ChatResult(content="Plan done", tool_calls=[])
    ]

    class FakeParentClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            r = parent_results[self.i]
            self.i += 1
            return r

    child_results = [
        ChatResult(content="Child plan conclusion", tool_calls=[])
    ]

    class FakeChildClient:
        def chat(self, messages, tools):
            return child_results[0]

    monkeypatch.setattr(planmod, "build_chat_client", lambda profile: FakeParentClient())
    monkeypatch.setattr(submod, "build_chat_client", lambda profile: FakeChildClient())

    res = RuntimeApp(tmp_path).run_task("plan subagent task", profile_name="mock", mode="plan", assume_yes=True)

    assert res.status == "success"
    assert res.answer == "Plan done"


def test_system_prompts_advertise_dispatch():
    # loop/plan 的系统提示与 dispatch 描述要把"何时委派 / 何时直接读"告诉模型，提升真模型采纳率。
    from xhx_agent.orchestrators.loop import LOOP_SYSTEM_PROMPT
    from xhx_agent.orchestrators.plan import PLAN_SYSTEM_PROMPT

    assert "dispatch" in LOOP_SYSTEM_PROMPT
    assert "dispatch" in PLAN_SYSTEM_PROMPT
    desc = default_tool_registry().definition("dispatch").description
    assert "read_file" in desc  # 指引"单个已知文件直接读"，避免过度委派
