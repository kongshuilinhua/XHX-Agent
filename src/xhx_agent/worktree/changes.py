from __future__ import annotations

import logging
from dataclasses import dataclass

from xhx_agent.worktree._git import run_git

log = logging.getLogger(__name__)


@dataclass
class Changes:
    uncommitted: int = 0
    new_commits: int = 0


def count_worktree_changes(wt_path: str, head_commit: str) -> Changes:
    import subprocess

    changes = Changes()
    try:
        status = run_git(["status", "--porcelain"], cwd=wt_path)
        if status.returncode == 0:
            changes.uncommitted = len([line for line in status.stdout.splitlines() if line.strip()])
    except (subprocess.SubprocessError, OSError):
        changes.uncommitted = 1

    try:
        rev_list = run_git(["rev-list", "--count", f"{head_commit}..HEAD"], cwd=wt_path)
        if rev_list.returncode == 0:
            changes.new_commits = int(rev_list.stdout.strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        changes.new_commits = 1

    return changes


def has_worktree_changes(wt_path: str, head_commit: str) -> bool:
    c = count_worktree_changes(wt_path, head_commit)
    return c.uncommitted > 0 or c.new_commits > 0


@dataclass
class CleanupResult:
    kept: bool
    path: str = ""
    branch: str = ""


def has_unpushed_commits(wt_path: str) -> bool:
    import subprocess

    try:
        result = run_git(
            ["rev-list", "--max-count=1", "HEAD", "--not", "--remotes"],
            cwd=wt_path,
        )
        return bool(result.stdout.strip()) if result.returncode == 0 else True
    except (subprocess.SubprocessError, OSError):
        return True
