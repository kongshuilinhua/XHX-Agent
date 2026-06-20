"""trace / teams.progress / TaskGet / TeamManager 单测。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.agents.trace import TraceManager
from xhx_agent.teams.models import TeammateInfo
from xhx_agent.teams.progress import TeammateProgress, ToolActivity, _describe
from xhx_agent.tools.task_get import TaskGetParams, TaskGetTool

# --- TraceManager ---


def test_trace_manager_lifecycle() -> None:
    tm = TraceManager()
    node = tm.create("Explore", trace_id="trace1")
    aid = node.agent_id
    assert tm.get(aid) is node
    tm.update(aid, input_tokens=10, output_tokens=4)
    assert tm.get(aid).input_tokens == 10
    child = tm.create("Plan", parent_id=aid, trace_id="trace1")
    tm.update(child.agent_id, input_tokens=5, output_tokens=1)
    assert len(tm.get_tree("trace1")) == 2
    tin, tout = tm.get_total_tokens("trace1")
    assert tin == 15 and tout == 5
    tm.complete(aid, status="completed")
    assert tm.get(aid).status == "completed"
    tm.complete_all_running(aid)  # child 的 parent 是 aid → 标记完成
    assert tm.get(child.agent_id).status == "completed"
    tm.remove(aid)
    assert tm.get(aid) is None


# --- teams/progress ---


def test_tool_activity_describe() -> None:
    assert "Reading" in _describe("read_file", {"path": "a.py"})
    assert "Patching" in _describe("apply_patch", {"path": "b.py"})
    assert "Searching" in _describe("search", {"glob": "*.py"})
    assert "Running" in _describe("terminal", {"command": "ls -la"})
    assert _describe("other", {}) == "other"
    act = ToolActivity.from_tool_use("read_file", {"path": "x"})
    assert act.tool_name == "read_file"


def test_teammate_progress() -> None:
    p = TeammateProgress(name="alice")
    assert p.spinner_verb  # __post_init__ 随机赋值
    for i in range(7):
        p.record_tool_use("read_file", {"path": f"f{i}"})
    assert p.tool_use_count == 7
    assert len(p.recent_activities) == 5  # 只保留最近 5 条
    assert p.activity_summary  # last_activity 的描述
    p.record_tokens(100, 50)
    assert p.token_count == 150
    assert TeammateProgress.format_tokens(1500) == "1.5k"
    assert TeammateProgress.format_tokens(2_000_000) == "2.0M"
    assert TeammateProgress.format_tokens(42) == "42"


def test_activity_summary_fallback() -> None:
    p = TeammateProgress(name="bob", spinner_verb="Purring")
    assert p.activity_summary == "Purring"  # 无活动 → spinner verb


# --- TaskGet ---


def test_task_get_tool(tmp_path: Path) -> None:
    from xhx_agent.teams.shared_task import SharedTaskStore

    store = SharedTaskStore(tmp_path / "tasks.json")
    t = store.create("写测试", description="补单测", assignee="alice", created_by="lead")
    store.update(t.id, add_blocked_by=["x"])

    class _TM:
        def get_task_store(self, name):
            return store

    tool = TaskGetTool(_TM(), "team1")
    res = asyncio.run(tool.execute(TaskGetParams(task_id=t.id)))
    assert "写测试" in res.output and "alice" in res.output and "Blocked by" in res.output

    miss = asyncio.run(tool.execute(TaskGetParams(task_id="999")))
    assert miss.is_error

    class _EmptyTM:
        def get_task_store(self, name):
            return None

    err = asyncio.run(TaskGetTool(_EmptyTM(), "x").execute(TaskGetParams(task_id="1")))
    assert err.is_error


# --- TeamManager ---


def test_team_manager(tmp_path: Path, monkeypatch) -> None:
    teams_root = tmp_path / "teams"
    monkeypatch.setattr("xhx_agent.teams.manager.resolve_team_dir", lambda name: teams_root / name)
    monkeypatch.setattr("xhx_agent.teams.models.resolve_team_dir", lambda name: teams_root / name)

    from xhx_agent.teams.manager import TeamManager

    mgr = TeamManager()
    team = mgr.create_team("squad", lead_agent_id="lead")
    assert mgr.get_task_store(team.name) is not None
    assert mgr.get_mailbox(team.name) is not None

    member = TeammateInfo(name="alice-unique-xyz", agent_id="aid-xyz", agent_type="coder", model="m", worktree_path="")
    mgr.register_member(team.name, member)
    assert mgr.get_team_for_teammate("aid-xyz") == team.name

    # set_member_idle → 给 lead 发消息
    mgr.set_member_idle(team.name, "alice-unique-xyz")
    drained = mgr.drain_lead_mailbox(team.name)
    assert any("idle" in m.content for m in drained)

    # on_teammate_completed 定位团队并标记 idle（不报错）
    mgr.on_teammate_completed("aid-xyz")

    assert isinstance(mgr.get_all_teammate_progress(), list)

    mgr.delete_team(team.name)
    assert mgr.get_task_store(team.name) is None
