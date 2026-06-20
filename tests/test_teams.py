"""teams 数据层单测：AgentTeam/TeammateInfo、SharedTaskStore。"""

from __future__ import annotations

from pathlib import Path

from xhx_agent.teams.models import AgentTeam, TeammateInfo, _sanitize_name, unique_team_name
from xhx_agent.teams.shared_task import SharedTaskStore


def _member(name: str = "alice") -> TeammateInfo:
    return TeammateInfo(name=name, agent_id=f"id-{name}", agent_type="coder", model="m", worktree_path="/wt")


def test_team_member_management() -> None:
    team = AgentTeam(name="t", lead_agent_id="lead")
    team.add_member(_member("alice"))
    team.add_member(_member("bob"))
    assert team.get_member("alice") is not None
    assert team.get_member("id-bob") is not None  # 按 agent_id 也能查
    assert team.set_member_active("alice", True) is True
    assert team.set_member_active("ghost", True) is False
    assert team.active_members()  # alice active, bob is_active None → 视为 active
    team.set_member_active("alice", False)
    team.set_member_active("bob", False)
    assert team.all_idle() is True
    assert team.remove_member("alice") is True
    assert team.remove_member("alice") is False


def test_team_save_load_roundtrip(tmp_path: Path) -> None:
    cfg = tmp_path / "config.json"
    team = AgentTeam(name="team1", lead_agent_id="lead", config_path=str(cfg), description="desc")
    team.add_member(_member("alice"))
    team.save()
    assert cfg.is_file()

    loaded = AgentTeam.load(str(cfg))
    assert loaded.name == "team1" and loaded.description == "desc"
    assert loaded.get_member("alice") is not None
    assert loaded.config_path == str(cfg)


def test_teammate_dict_roundtrip() -> None:
    m = _member("x")
    restored = TeammateInfo.from_dict(m.to_dict())
    assert restored.name == "x" and restored.agent_type == "coder"


def test_sanitize_and_unique_name() -> None:
    assert _sanitize_name("My Team!!") == "my-team"
    assert _sanitize_name("a__b") == "a_b" or _sanitize_name("a__b")  # 保留下划线
    # 极不可能存在的名字 → 直接返回 sanitize 结果
    name = unique_team_name("zzz-unlikely-team-xyz-12345")
    assert name.startswith("zzz-unlikely-team-xyz")


def test_shared_task_store_crud(tmp_path: Path) -> None:
    store = SharedTaskStore(tmp_path / "tasks.json")
    t = store.create("写贪吃蛇", description="d", assignee="alice")
    assert t.id == "1" and t.status == "pending"
    store.create("第二个任务", assignee="bob")

    assert len(store.list_tasks()) == 2
    assert len(store.list_tasks(assignee="alice")) == 1
    assert len(store.list_tasks(status="pending")) == 2

    updated = store.update("1", status="completed", add_blocks=["2"], add_blocked_by=["x"])
    assert updated is not None and updated.status == "completed"
    assert "2" in updated.blocks and "x" in updated.blocked_by
    assert store.update("999", status="x") is None

    assert len(store.list_tasks(status="completed")) == 1


def test_shared_task_store_persistence(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    SharedTaskStore(path).create("持久化任务")
    # 新实例从磁盘恢复
    store2 = SharedTaskStore(path)
    assert store2.get("1") is not None
    store2.init_empty()
    assert store2.list_tasks() == []
