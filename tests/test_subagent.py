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
            tool_calls=[
                ToolCall(
                    id="p1",
                    name="dispatch",
                    arguments={
                        "description": "explore readme content",
                        "prompt": "Read the readme file and tell me what is in it",
                        "agent_type": "explore",
                    },
                )
            ],
        ),
        # Turn 1: final answer
        ChatResult(content="Parent done", tool_calls=[]),
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
        ChatResult(content=None, tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "README.md"})]),
        # Turn 1: conclusion
        ChatResult(content="Child conclusion: README.md says Hello World", tool_calls=[]),
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
    tool_msg = next(
        (m for m in seen_parent_messages if m.get("role") == "tool" and m.get("tool_call_id") == "p1"), None
    )
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
            tool_calls=[
                ToolCall(
                    id="p1",
                    name="dispatch",
                    arguments={"description": "try update", "prompt": "try update", "agent_type": "explore"},
                )
            ],
        ),
        ChatResult(content="Parent done", tool_calls=[]),
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
            tool_calls=[
                ToolCall(
                    id="c1",
                    name="apply_patch",
                    arguments={
                        "patch": "*** Begin Patch\n*** Update File: target.py\n@@\n-original = 1\n+original = 2\n*** End Patch\n"
                    },
                )
            ],
        ),
        ChatResult(content="Child concluded", tool_calls=[]),
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


def test_subagent_turn_limit(tmp_path, monkeypatch):
    import xhx_agent.orchestrators._toolturn as toolturnmod
    import xhx_agent.orchestrators.subagent as submod

    class InfiniteToolClient:
        def chat(self, messages, tools):
            return ChatResult(
                content=None, tool_calls=[ToolCall(id="t1", name="search", arguments={"query": "infinite"})]
            )

    monkeypatch.setattr(submod, "build_chat_client", lambda profile: InfiniteToolClient())
    monkeypatch.setattr(toolturnmod, "_execute_tool_call_rich", lambda ctx, tc, turn: (tc, "infinite result", [], None))

    ctx = MagicMock()
    ctx.original_workspace = tmp_path  # routing 解析需要真实 workspace（无 .xhx 即回退默认配置）
    ctx.profile.name = "mock"
    ctx.kernel.tool_registry.tool_schemas.return_value = [{"function": {"name": "search", "parameters": {}}}]

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
            tool_calls=[
                ToolCall(
                    id="p1",
                    name="dispatch",
                    arguments={"description": "plan explore", "prompt": "read README.md", "agent_type": "explore"},
                )
            ],
        ),
        ChatResult(content="Plan done", tool_calls=[]),
    ]

    class FakeParentClient:
        def __init__(self):
            self.i = 0

        def chat(self, messages, tools):
            r = parent_results[self.i]
            self.i += 1
            return r

    child_results = [ChatResult(content="Child plan conclusion", tool_calls=[])]

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


def test_merge_into_parent_conflict_detection(tmp_path):
    import threading
    from unittest.mock import MagicMock

    from xhx_agent.orchestrators.subagent import _merge_into_parent

    # Setup directories
    parent_workspace = tmp_path / "parent"
    parent_workspace.mkdir()
    merge_root = tmp_path / "child"
    merge_root.mkdir()

    # Create dummy parent files
    file_a = parent_workspace / "file_a.txt"
    file_b = parent_workspace / "file_b.txt"
    file_a.write_text("parent a", encoding="utf-8")
    file_b.write_text("parent b", encoding="utf-8")

    # Create dummy child files
    child_file_a = merge_root / "file_a.txt"
    child_file_b = merge_root / "file_b.txt"
    child_file_a.write_text("child a", encoding="utf-8")
    child_file_b.write_text("child b", encoding="utf-8")

    # Mock ctx
    ctx = MagicMock()
    ctx.tool_context.workspace = parent_workspace
    ctx.subagent_claims = {}
    ctx.subagent_lock = threading.Lock()

    # Call merge_into_parent for file_a under label "agent_1"
    applied, conflicts = _merge_into_parent(ctx, merge_root, ["file_a.txt"], "agent_1")
    assert applied == ["file_a.txt"]
    assert conflicts == []
    assert ctx.subagent_claims["file_a.txt"] == "agent_1"
    assert file_a.read_text(encoding="utf-8") == "child a"

    # Call merge_into_parent for file_a under label "agent_2" (conflict expected)
    applied_2, conflicts_2 = _merge_into_parent(ctx, merge_root, ["file_a.txt"], "agent_2")
    assert applied_2 == []
    assert conflicts_2 == ["file_a.txt"]
    assert ctx.subagent_claims["file_a.txt"] == "agent_1"  # first claimant wins
    assert file_a.read_text(encoding="utf-8") == "child a"  # file content not overwritten by agent_2

    # Call merge_into_parent for file_b under label "agent_2" (no conflict, different file)
    applied_3, conflicts_3 = _merge_into_parent(ctx, merge_root, ["file_b.txt"], "agent_2")
    assert applied_3 == ["file_b.txt"]
    assert conflicts_3 == []
    assert ctx.subagent_claims["file_b.txt"] == "agent_2"
    assert file_b.read_text(encoding="utf-8") == "child b"


def test_sub_run_id_uses_uuid(tmp_path, monkeypatch):
    import uuid
    from unittest.mock import MagicMock

    import xhx_agent.orchestrators.subagent as submod
    from xhx_agent.orchestrators.base import OrchestratorContext

    ctx = OrchestratorContext(
        app=MagicMock(),
        task="test",
        run_id="test-run",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=MagicMock(),
        scan=MagicMock(),
        evidence=MagicMock(),
        kernel=MagicMock(),
        tool_context=MagicMock(),
    )

    # Mock uuid4 to return a fixed uuid
    fake_uuid = MagicMock()
    fake_uuid.hex = "abcdef123456"
    monkeypatch.setattr(uuid, "uuid4", lambda: fake_uuid)

    # Let's mock _drive_write_loop and _merge_into_parent so we can run run_write_subagent without hitting git/LLM
    monkeypatch.setattr(submod, "_drive_write_loop", lambda ctx, prompt, allowed, turn: ("result", []))
    monkeypatch.setattr(submod, "_merge_into_parent", lambda ctx, merge_root, changed, label: ([], []))

    # Mock WorktreeContext to do nothing
    class FakeWorktreeContext:
        def __init__(self, workspace, sub_run_id):
            self.sub_run_id = sub_run_id
            self.active_path = workspace
            self.is_active = True

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

    import xhx_agent.safety.worktree as wtmod

    monkeypatch.setattr(wtmod, "WorktreeContext", FakeWorktreeContext)

    # Let's run to verify run_write_subagent uses uuid-based run id and does not rely on len(subagent_claims)
    wt_instances = []

    def intercept_wt(workspace, sub_run_id):
        wt_instances.append(sub_run_id)
        return FakeWorktreeContext(workspace, sub_run_id)

    monkeypatch.setattr(wtmod, "WorktreeContext", intercept_wt)

    submod.run_write_subagent(ctx, description="test", prompt="edit prompt", turn=1)
    assert len(wt_instances) == 1
    assert "test-run-edit1-abcdef12" in wt_instances[0]


def test_run_write_subagent_seeds_prior_changed_files(tmp_path, monkeypatch):
    import subprocess
    import threading
    from unittest.mock import MagicMock

    import xhx_agent.orchestrators.subagent as submod
    from xhx_agent.orchestrators.base import OrchestratorContext
    from xhx_agent.tools.registry import ToolContext

    # 1. Initialize git repo
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    # Create an initial commit
    init_file = tmp_path / "init.txt"
    init_file.write_text("initial commit file", encoding="utf-8")
    subprocess.run(["git", "add", "init.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    # 2. Write foo.py in the parent workspace (not committed, simulating round 1 edits)
    foo_file = tmp_path / "foo.py"
    foo_file.write_text("line1\n", encoding="utf-8")

    # 3. Construct minimum ctx
    ctx = OrchestratorContext(
        app=MagicMock(),
        task="mock task",
        run_id="run-seeding-123",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=MagicMock(),
        scan=MagicMock(),
        evidence=MagicMock(),
        kernel=MagicMock(),
        tool_context=ToolContext(workspace=tmp_path),
        subagent_claims={},
    )
    ctx.subagent_lock = threading.Lock()

    # 4. Mock _drive_write_loop to assert seeding worked
    seeding_verified = False

    def fake_drive_write_loop(run_ctx, prompt, allowed, turn):
        nonlocal seeding_verified
        # The worktree workspace path:
        wt_workspace = run_ctx.tool_context.workspace
        wt_foo = wt_workspace / "foo.py"
        assert wt_foo.exists(), "foo.py was not seeded to worktree"
        assert wt_foo.read_text(encoding="utf-8") == "line1\n", "foo.py content mismatch"
        seeding_verified = True
        return "ok", []

    monkeypatch.setattr(submod, "_drive_write_loop", fake_drive_write_loop)
    monkeypatch.setattr(submod, "_merge_into_parent", lambda ctx, merge_root, changed, label: ([], []))

    # Run subagent with seeding
    conclusion, changed = submod.run_write_subagent(
        ctx, description="seeding_test", prompt="edit foo", turn=2, seed_files=["foo.py"]
    )
    assert seeding_verified, "_drive_write_loop was not run or assertion failed"
    assert "ok" in conclusion

    # 5. Counter-example: seed_files=None, foo.py should NOT exist in worktree
    no_seeding_verified = False

    def fake_drive_write_loop_no_seed(run_ctx, prompt, allowed, turn):
        nonlocal no_seeding_verified
        wt_workspace = run_ctx.tool_context.workspace
        wt_foo = wt_workspace / "foo.py"
        assert not wt_foo.exists(), "foo.py should not be seeded when seed_files=None"
        no_seeding_verified = True
        return "ok", []

    monkeypatch.setattr(submod, "_drive_write_loop", fake_drive_write_loop_no_seed)
    submod.run_write_subagent(ctx, description="no_seeding_test", prompt="edit foo", turn=3, seed_files=None)
    assert no_seeding_verified
