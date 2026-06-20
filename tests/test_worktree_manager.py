"""worktree/manager.py 单测：在临时 git 仓库里跑 create/list/enter/exit 生命周期。"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from xhx_agent.worktree.manager import WorktreeManager
from xhx_agent.worktree.slug import flatten_slug


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _init_repo(root: Path) -> bool:
    try:
        _git(root, "init")
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
    _git(root, "config", "user.email", "t@t.com")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    return True


def test_flatten_slug() -> None:
    assert flatten_slug("My Feature Branch") == flatten_slug("My Feature Branch")
    assert flatten_slug("a/b c") != ""


def test_manager_basics(tmp_path: Path) -> None:
    mgr = WorktreeManager(repo_root=str(tmp_path))
    assert mgr.list_worktrees() == []
    assert mgr.restore_session() is None


def test_enter_missing_raises(tmp_path: Path) -> None:
    mgr = WorktreeManager(repo_root=str(tmp_path))
    with pytest.raises(FileNotFoundError):
        asyncio.run(mgr.enter("nope"))


def test_create_list_exit_cycle(tmp_path: Path) -> None:
    if not _init_repo(tmp_path):
        pytest.skip("git not available")
    mgr = WorktreeManager(repo_root=str(tmp_path))

    async def _run() -> None:
        handle = await mgr.create("myfeature")
        assert handle.path.exists()
        assert handle.branch.startswith("xhx-wt-")
        assert len(mgr.list_worktrees()) == 1
        # 退出并移除
        await mgr.exit(handle.name, action="remove")
        assert mgr.list_worktrees() == []

    asyncio.run(_run())
