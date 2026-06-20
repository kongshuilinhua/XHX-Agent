"""团队工具单测：TaskUpdate / SendMessage。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.teams.mailbox import Mailbox
from xhx_agent.teams.models import AgentTeam, TeammateInfo
from xhx_agent.teams.shared_task import SharedTaskStore
from xhx_agent.tools.send_message import SendMessageParams, SendMessageTool
from xhx_agent.tools.task_update import TaskUpdateParams, TaskUpdateTool


def test_task_update_tool(tmp_path: Path) -> None:
    store = SharedTaskStore(tmp_path / "tasks.json")
    t = store.create("活儿", assignee="")

    class _TM:
        def get_task_store(self, name):
            return store

    tool = TaskUpdateTool(_TM(), "team1")
    res = asyncio.run(
        tool.execute(TaskUpdateParams(task_id=t.id, status="in_progress", assignee="alice", add_blocks=["2"]))
    )
    assert "status → in_progress" in res.output and "alice" in res.output
    assert store.get(t.id).status == "in_progress"

    # 非法状态
    bad = asyncio.run(tool.execute(TaskUpdateParams(task_id=t.id, status="bogus")))
    assert bad.is_error
    # 任务不存在
    miss = asyncio.run(tool.execute(TaskUpdateParams(task_id="999", status="completed")))
    assert miss.is_error

    class _EmptyTM:
        def get_task_store(self, name):
            return None

    err = asyncio.run(TaskUpdateTool(_EmptyTM(), "x").execute(TaskUpdateParams(task_id="1")))
    assert err.is_error


class _FakeTM:
    def __init__(self, tmp_path: Path) -> None:
        self._mailbox = Mailbox(tmp_path / "mb")
        self._team = AgentTeam(name="team1", lead_agent_id="lead-id")
        self._team.add_member(TeammateInfo(name="alice", agent_id="aid-a", agent_type="c", model="m", worktree_path=""))

    def get_mailbox(self, name):
        return self._mailbox

    def get_team(self, name):
        return self._team


def test_send_message_to_lead(tmp_path: Path) -> None:
    tm = _FakeTM(tmp_path)
    tool = SendMessageTool(tm, "team1", "alice")
    res = asyncio.run(tool.execute(SendMessageParams(to="lead", content="进展汇报")))
    assert res.is_error is False
    msgs = tm.get_mailbox("team1").read("lead-id")
    assert msgs and "进展汇报" in msgs[0].content


def test_send_message_to_teammate(tmp_path: Path) -> None:
    tm = _FakeTM(tmp_path)
    tool = SendMessageTool(tm, "team1", "lead")
    res = asyncio.run(tool.execute(SendMessageParams(to="alice", content="去做任务")))
    assert res.is_error is False
    assert tm.get_mailbox("team1").read("aid-a")


def test_send_message_unknown_recipient(tmp_path: Path) -> None:
    tm = _FakeTM(tmp_path)
    tool = SendMessageTool(tm, "team1", "lead")
    res = asyncio.run(tool.execute(SendMessageParams(to="ghost", content="x")))
    assert res.is_error and "not found" in res.output


def test_send_message_no_mailbox() -> None:
    class _NoMB:
        def get_mailbox(self, name):
            return None

    res = asyncio.run(SendMessageTool(_NoMB(), "t", "a").execute(SendMessageParams(to="lead", content="x")))
    assert res.is_error
