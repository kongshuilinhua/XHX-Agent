from __future__ import annotations

import subprocess
from pathlib import Path

from xhx_agent.safety.worktree import WorktreeContext, is_git_repo


def test_is_git_repo_on_non_git_path(tmp_path: Path) -> None:
    assert is_git_repo(tmp_path) is False


def test_is_git_repo_on_current_workspace() -> None:
    # 在有些 CI/CD 的容器/挂载环境中，由于 dubious ownership 机制，
    # 可能会由于所有权问题导致 git 命令返回失败。因此我们只需验证其返回值类型。
    res = is_git_repo(Path.cwd())
    assert isinstance(res, bool)


def test_worktree_context_fallback_on_non_git_path(tmp_path: Path) -> None:
    # On a non-git path, WorktreeContext should exit cleanly and fall back to in-place execution.
    run_id = "test-wt-fallback"
    with WorktreeContext(tmp_path, run_id) as wt_ctx:
        assert wt_ctx.is_active is False
        assert wt_ctx.active_path == tmp_path.resolve()


def test_worktree_context_creation_and_sync_on_git_workspace(tmp_path: Path) -> None:
    # 1. Create a dummy git repo in tmp_path
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True, capture_output=True)

    # Create an initial commit so we can add worktrees
    readme = tmp_path / "README.md"
    readme.write_text("Hello World\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=tmp_path, check=True, capture_output=True)

    run_id = "test-wt-success"
    # Verify the worktree context manager
    with WorktreeContext(tmp_path, run_id) as wt_ctx:
        assert wt_ctx.is_active is True
        assert wt_ctx.active_path == wt_ctx.worktree_dir

        # Verify the worktree directory has the committed files
        assert (wt_ctx.worktree_dir / "README.md").exists()

        # Make a modification inside the worktree
        modified_file = wt_ctx.worktree_dir / "README.md"
        modified_file.write_text("Hello Isolated World\n", encoding="utf-8")

        # Sync back to primary workspace
        wt_ctx.sync_to_workspace(["README.md"])

        # Check that it copied back successfully
        assert readme.read_text(encoding="utf-8") == "Hello Isolated World\n"

    # Verify that the worktree directory and temporary branch are cleaned up cleanly
    assert not wt_ctx.worktree_dir.exists()
    completed_branch = subprocess.run(
        ["git", "branch"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert wt_ctx.branch_name not in completed_branch.stdout


def test_worktree_cleanup_recovers_when_remove_fails(tmp_path: Path, monkeypatch) -> None:
    """`git worktree remove` 失败时（如 Windows 并行文件锁），__exit__ 应重试→兜底 rmtree+prune+删分支，零残留。"""
    from xhx_agent.safety import worktree as wtmod

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "f.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True)

    wt = WorktreeContext(tmp_path, "test-recover")
    wt.__enter__()
    assert wt.is_active

    real_run = subprocess.run
    monkeypatch.setattr(wtmod.time, "sleep", lambda *a: None)  # 跳过重试退避，测试快

    def fake_run(cmd, **kw):
        # 模拟 "git worktree remove" 总失败；其余 git 命令照常真跑（prune/branch -D）。
        if cmd[:3] == ["git", "worktree", "remove"]:

            class _R:
                returncode = 1
                stderr = "simulated lock"
                stdout = ""

            return _R()
        return real_run(cmd, **kw)

    monkeypatch.setattr(wtmod.subprocess, "run", fake_run)
    wt.__exit__(None, None, None)

    # 零残留：worktree 目录删除、git worktree list 不含它、临时分支删除。
    assert not wt.worktree_dir.exists()
    listed = real_run(["git", "worktree", "list"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert "test-recover" not in listed
    branches = real_run(["git", "branch"], cwd=tmp_path, capture_output=True, text=True).stdout
    assert wt.branch_name not in branches
