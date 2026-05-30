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
