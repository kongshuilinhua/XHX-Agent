"""TeamCreate/TeamDelete/ExitWorktree 工具回归。

这三个工具曾是空壳（execute 直接返回硬编码字符串，不碰 manager）。本测试用真
TeamManager / WorktreeManager 验证它们真正干活：建队能 get_team、删队后消失、
退出 worktree 真把它从 active 移除，以及 remove 对未提交改动的保护。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path


class _FakeAgent:
    agent_id = "lead123"


def _git_init(d: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)


def test_team_create_and_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.teams.manager import TeamManager
    from xhx_agent.tools.team_create import TeamCreateParams, TeamCreateTool
    from xhx_agent.tools.team_delete import TeamDeleteParams, TeamDeleteTool

    async def run() -> None:
        tm = TeamManager()
        tc = TeamCreateTool(team_manager=tm, parent_agent=_FakeAgent(), teammate_mode="", is_interactive=True)
        r = await tc.execute(TeamCreateParams(name="alpha", description="d"))
        assert not r.is_error, r.output
        name = next(iter(tm._teams))
        assert tm.get_team(name) is not None  # 真落盘 + 在册

        td = TeamDeleteTool(team_manager=tm, parent_agent=_FakeAgent())
        r2 = await td.execute(TeamDeleteParams(name=name))
        assert not r2.is_error, r2.output
        assert tm.get_team(name) is None  # 真删除

    asyncio.run(run())


def test_team_create_requires_manager(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.tools.team_create import TeamCreateParams, TeamCreateTool

    async def run() -> None:
        r = await TeamCreateTool().execute(TeamCreateParams(name="x"))
        assert r.is_error  # 未配置 manager/agent 时报错而非假装成功

    asyncio.run(run())


def test_team_delete_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.teams.manager import TeamManager
    from xhx_agent.tools.team_delete import TeamDeleteParams, TeamDeleteTool

    async def run() -> None:
        r = await TeamDeleteTool(team_manager=TeamManager()).execute(TeamDeleteParams(name="nope"))
        assert r.is_error and "not found" in r.output

    asyncio.run(run())


def test_exit_worktree_noop_and_keep(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _git_init(tmp_path)
    from xhx_agent.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
    from xhx_agent.worktree.manager import WorktreeManager

    async def run() -> None:
        wm = WorktreeManager(project_root=str(tmp_path))
        ew = ExitWorktreeTool(worktree_manager=wm)

        # 无 active worktree → no-op（而非假装退出成功）
        r = await ew.execute(ExitWorktreeParams(action="keep"))
        assert r.is_error and "No-op" in r.output

        # 真建 worktree → keep 退出 → 真从 active 移除
        await wm.create("feature-x")
        assert len(wm.list_worktrees()) == 1
        r2 = await ew.execute(ExitWorktreeParams(action="keep"))
        assert not r2.is_error, r2.output
        assert len(wm.list_worktrees()) == 0

    asyncio.run(run())


def test_exit_worktree_remove_guards_uncommitted(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _git_init(tmp_path)
    from xhx_agent.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
    from xhx_agent.worktree.manager import WorktreeManager

    async def run() -> None:
        wm = WorktreeManager(project_root=str(tmp_path))
        ew = ExitWorktreeTool(worktree_manager=wm)
        handle = await wm.create("feature-y")
        # 在 worktree 里制造未提交改动
        (handle.path / "new.txt").write_text("dirty")

        # remove 未确认 discard → 被保护拒绝，worktree 仍在 active
        r = await ew.execute(ExitWorktreeParams(action="remove", discard_changes=False))
        assert r.is_error and "discard_changes" in r.output
        assert len(wm.list_worktrees()) == 1

        # discard_changes=true → 真删除
        r2 = await ew.execute(ExitWorktreeParams(action="remove", discard_changes=True))
        assert not r2.is_error, r2.output
        assert len(wm.list_worktrees()) == 0

    asyncio.run(run())
