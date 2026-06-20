"""杂项工具单测：git_ops 差异、search 检索、TaskList 工具。"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from xhx_agent.runtime.git_ops import GitOps, _is_git_worktree
from xhx_agent.tools.search import _python_search, search


def test_git_ops_no_changed_files(tmp_path: Path) -> None:
    summary = GitOps(tmp_path).diff_changed_files([])
    assert summary.changed_files == [] and "No changed files" in summary.summary


def test_git_ops_outside_worktree(tmp_path: Path) -> None:
    assert _is_git_worktree(tmp_path) is False
    summary = GitOps(tmp_path).diff_changed_files(["a.py"])
    assert "outside a git worktree" in summary.summary


def test_git_ops_real_repo(tmp_path: Path) -> None:
    def _git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True)

    try:
        _git("init")
    except (FileNotFoundError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    _git("config", "user.email", "t@t.com")
    _git("config", "user.name", "t")
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    _git("add", "a.py")
    _git("commit", "-m", "init")
    f.write_text("v2-modified\n", encoding="utf-8")

    summary = GitOps(tmp_path).diff_changed_files(["a.py"])
    assert "a.py" in summary.changed_files
    assert "v2-modified" in summary.diff_text or summary.diff_text  # 有 diff 输出


def test_python_search(tmp_path: Path) -> None:
    (tmp_path / "code.py").write_text("alpha\nneedle here\nbeta\n", encoding="utf-8")
    (tmp_path / "other.txt").write_text("needle too\n", encoding="utf-8")
    results = _python_search(tmp_path, tmp_path, "needle", None, 50)
    assert any("needle here" in r for r in results)


def test_search_finds_match(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("xyzzy_marker = 1\n", encoding="utf-8")
    results = search(tmp_path, "xyzzy_marker")
    assert any("xyzzy_marker" in r for r in results)


def test_tasklist_tool(tmp_path: Path) -> None:
    from xhx_agent.tools.task_list import TaskListParams, TaskListTool

    store = __import__("xhx_agent.teams.shared_task", fromlist=["SharedTaskStore"]).SharedTaskStore(
        tmp_path / "tasks.json"
    )
    store.create("任务A", assignee="alice")
    store.create("任务B")

    class _TM:
        def get_task_store(self, name):
            return store

    tool = TaskListTool(_TM(), "team1")
    res = asyncio.run(tool.execute(TaskListParams()))
    assert "任务A" in res.output and "任务B" in res.output
    res2 = asyncio.run(tool.execute(TaskListParams(assignee="alice")))
    assert "任务A" in res2.output and "任务B" not in res2.output

    class _EmptyTM:
        def get_task_store(self, name):
            return None

    res3 = asyncio.run(TaskListTool(_EmptyTM(), "x").execute(TaskListParams()))
    assert res3.is_error is True
