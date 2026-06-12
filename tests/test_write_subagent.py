import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import xhx_agent.orchestrators._toolturn as toolturnmod
import xhx_agent.orchestrators.subagent as submod
from xhx_agent.models.types import ChatResult, ToolCall
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.orchestrators.subagent import (
    AGENT_TOOLSETS,
    WRITE_AGENT_TYPES,
    _merge_into_parent,
    run_write_subagent,
)
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import ToolContext, default_tool_registry


# Checkpoint 1: toolset/schema
def test_checkpoint_1_toolset_schema():
    assert "apply_patch" in AGENT_TOOLSETS["edit"]
    assert {"edit"} == WRITE_AGENT_TYPES

    registry = default_tool_registry()
    dispatch_def = registry.definition("dispatch")
    assert dispatch_def is not None
    agent_type_prop = dispatch_def.parameters["properties"]["agent_type"]
    assert "edit" in agent_type_prop["enum"]


# Checkpoint 2: 路由
def test_checkpoint_2_routing(monkeypatch):
    called_write = []
    called_read = []

    def fake_run_write(ctx, *, description, prompt, turn):
        called_write.append((description, prompt, turn))
        return "write conclusion", ["modified.py"]

    def fake_run_subagent(ctx, *, description, prompt, agent_type, turn):
        called_read.append((description, prompt, agent_type, turn))
        return "read conclusion"

    monkeypatch.setattr(submod, "run_write_subagent", fake_run_write)
    monkeypatch.setattr(submod, "run_subagent", fake_run_subagent)

    ctx = SimpleNamespace(
        event_callback=None,
        evidence=MagicMock(),
        kernel=MagicMock()
    )

    # 1. agent_type="edit" -> run_write_subagent
    tc_write = ToolCall(id="p1", name="dispatch", arguments={
        "agent_type": "edit",
        "description": "desc write",
        "prompt": "prompt write"
    })
    tc, content, changed, meta = toolturnmod._execute_tool_call_rich(ctx, tc_write, 1)

    assert len(called_write) == 1
    assert called_write[0] == ("desc write", "prompt write", 1)
    assert content == "write conclusion"
    assert changed == ["modified.py"]

    # 2. agent_type="explore" (or explore default) -> run_subagent
    tc_read = ToolCall(id="p2", name="dispatch", arguments={
        "agent_type": "explore",
        "description": "desc read",
        "prompt": "prompt read"
    })
    tc2, content2, changed2, meta2 = toolturnmod._execute_tool_call_rich(ctx, tc_read, 1)

    assert len(called_read) == 1
    assert called_read[0] == ("desc read", "prompt read", "explore", 1)
    assert content2 == "read conclusion"
    assert changed2 == []


# Checkpoint 3: 隔离 + 合并 (端到端)
def test_checkpoint_3_worktree_isolation_and_merge(tmp_path, monkeypatch):
    # Initialize git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    readme = tmp_path / "README.md"
    readme.write_text("Hello World\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=tmp_path, check=True)

    RuntimeApp(tmp_path).init_project()

    parent_results = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="p1", name="dispatch", arguments={
                "description": "edit subagent task",
                "prompt": "change README.md using apply_patch",
                "agent_type": "edit"
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
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={
                "patch": """--- a/README.md
+++ b/README.md
@@ -1,1 +1,1 @@
-Hello World
+Hello Subagent
"""
            })]
        ),
        ChatResult(content="Child done", tool_calls=[])
    ]

    class FakeChildClient:
        def __init__(self):
            self.i = 0
        def chat(self, messages, tools):
            r = child_results[self.i]
            self.i += 1
            return r

    import xhx_agent.orchestrators.loop as loopmod
    original_build_routed_client = loopmod.build_routed_client

    def fake_build_routed_client(workspace, *, role, base_profile_name, event_callback=None, build_client_func=None):
        if role == "loop":
            return FakeParentClient()
        elif role == "edit":
            return FakeChildClient()
        return original_build_routed_client(workspace, role=role, base_profile_name=base_profile_name, event_callback=event_callback, build_client_func=build_client_func)

    monkeypatch.setattr(loopmod, "build_routed_client", fake_build_routed_client)
    monkeypatch.setattr(submod, "build_routed_client", fake_build_routed_client)

    res = RuntimeApp(tmp_path).run_task("change readme", profile_name="mock", mode="loop")
    assert res.status == "success"

    # Check that README.md has been changed in the parent workspace after merge
    assert readme.read_text(encoding="utf-8").strip() == "Hello Subagent"


# Checkpoint 4: 冲突上报 (先到先得)
def test_checkpoint_4_conflicts_and_claims(tmp_path):
    parent_workspace = tmp_path / "parent"
    parent_workspace.mkdir()
    child1_workspace = tmp_path / "child1"
    child1_workspace.mkdir()
    child2_workspace = tmp_path / "child2"
    child2_workspace.mkdir()

    f1 = child1_workspace / "a.py"
    f1.write_text("child1 content", encoding="utf-8")

    f2 = child2_workspace / "a.py"
    f2.write_text("child2 content", encoding="utf-8")

    claims = {}
    ctx = SimpleNamespace(
        tool_context=ToolContext(workspace=parent_workspace),
        subagent_claims=claims,
        event_callback=None
    )

    # Merge child1 -> should claim file and apply
    applied1, conflicts1 = _merge_into_parent(ctx, child1_workspace, ["a.py"], "agent1")
    assert applied1 == ["a.py"]
    assert conflicts1 == []
    assert claims["a.py"] == "agent1"
    assert (parent_workspace / "a.py").read_text(encoding="utf-8") == "child1 content"

    # Merge child2 -> conflict! Should keep agent1 version
    applied2, conflicts2 = _merge_into_parent(ctx, child2_workspace, ["a.py"], "agent2")
    assert applied2 == []
    assert conflicts2 == ["a.py"]
    assert (parent_workspace / "a.py").read_text(encoding="utf-8") == "child1 content"


def test_run_write_subagent_conflict(tmp_path, monkeypatch):
    # Initialize parent git
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "a.py").write_text("parent\n", encoding="utf-8")
    subprocess.run(["git", "add", "a.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    # Setup claims and context
    claims = {"a.py": "first_agent"}
    events = []

    ctx = OrchestratorContext(
        app=MagicMock(),
        task="mock task",
        run_id="run-123",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=SimpleNamespace(name="mock"),
        scan=MagicMock(),
        evidence=MagicMock(),
        kernel=SafeExecutionKernel(tmp_path, "run-123", MagicMock(), default_tool_registry()),
        tool_context=ToolContext(workspace=tmp_path),
        event_callback=lambda e: events.append(e),
        subagent_claims=claims,
    )

    # Mock child client to return a write result modifying a.py
    class FakeChildClient:
        def chat(self, messages, tools):
            if not hasattr(self, "called"):
                self.called = True
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={
                        "patch": """--- a/a.py
+++ b/a.py
@@ -1,1 +1,1 @@
-parent
+child2
"""
                    })]
                )
            return ChatResult(content="Done", tool_calls=[])

    monkeypatch.setattr(submod, "build_routed_client", lambda *args, **kwargs: FakeChildClient())

    # Run subagent
    conclusion, changed = run_write_subagent(ctx, description="second_agent", prompt="edit a.py")

    # Verify conflicts
    assert "CONFLICT" in conclusion
    assert "a.py" not in changed

    # event should contain conflicts
    done_event = next(e for e in events if e.type == "subagent_done")
    assert done_event.payload["conflicts"] == ["a.py"]

    # Parent file must remain parent version
    assert (tmp_path / "a.py").read_text(encoding="utf-8").strip() == "parent"


# Checkpoint 5: 非 git 降级
def test_checkpoint_5_non_git_fallback(tmp_path, monkeypatch):
    # tmp_path has no git repo
    (tmp_path / "b.py").write_text("original\n", encoding="utf-8")

    claims = {}
    ctx = OrchestratorContext(
        app=MagicMock(),
        task="mock task",
        run_id="run-123",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=SimpleNamespace(name="mock"),
        scan=MagicMock(),
        evidence=MagicMock(),
        kernel=SafeExecutionKernel(tmp_path, "run-123", MagicMock(), default_tool_registry()),
        tool_context=ToolContext(workspace=tmp_path),
        event_callback=MagicMock(),
        subagent_claims=claims,
    )

    class FakeChildClient:
        def chat(self, messages, tools):
            if not hasattr(self, "called"):
                self.called = True
                return ChatResult(
                    content=None,
                    tool_calls=[ToolCall(id="c1", name="apply_patch", arguments={
                        "patch": """--- a/b.py
+++ b/b.py
@@ -1,1 +1,1 @@
-original
+modified
"""
                    })]
                )
            return ChatResult(content="Done", tool_calls=[])

    monkeypatch.setattr(submod, "build_routed_client", lambda *args, **kwargs: FakeChildClient())

    # run_write_subagent should not raise git errors, should modify b.py directly
    conclusion, changed = run_write_subagent(ctx, description="edit_b", prompt="edit b.py")

    assert "Done" in conclusion or "edit sub-agent finished" in conclusion
    assert "b.py" in changed
    assert (tmp_path / "b.py").read_text(encoding="utf-8").strip() == "modified"
