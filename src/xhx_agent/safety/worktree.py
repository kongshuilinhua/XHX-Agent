"""Git worktree 隔离：在仓库内开一个临时 worktree 跑改动，成功才同步回主工作区。

这是「失败自动回滚」的实现基础——失败时直接丢弃整个 worktree，主工作区毫发无损。
非 git 仓库（或建 worktree 失败）则优雅降级为就地执行，此时没有自动回滚（由上层 Restore Plan 兜底）。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def is_git_repo(workspace: Path) -> bool:
    """workspace 是否在 git 工作区内（worktree 隔离的前提）。"""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workspace,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"
    except Exception:
        return False


class WorktreeContext:
    """上下文管理器：进入时尝试建隔离 worktree，退出时清理 worktree 与临时分支。

    is_active 表示隔离是否真的生效；为 False 时所有操作落在主工作区（就地执行，无自动回滚）。
    """

    def __init__(self, workspace: Path, run_id: str) -> None:
        self.workspace = workspace.resolve()
        self.run_id = run_id
        self.branch_name = f"xhx-wt-{run_id}"
        self.worktree_dir = self.workspace / ".xhx" / "worktrees" / f"wt-{run_id}"
        self.active_path = self.workspace
        self.is_active = False

    def __enter__(self) -> WorktreeContext:
        if not is_git_repo(self.workspace):
            logger.info("Not a git repository. Running in-place without worktree isolation.")
            return self

        try:
            self.worktree_dir.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Adding Git worktree for isolation: %s", self.worktree_dir)
            completed = subprocess.run(
                ["git", "worktree", "add", "-b", self.branch_name, str(self.worktree_dir)],
                cwd=self.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                logger.warning(
                    "Failed to create Git worktree: %s. Falling back to in-place execution.", completed.stderr
                )
                return self

            self.active_path = self.worktree_dir
            self.is_active = True
        except Exception as e:
            logger.warning("Error creating Git worktree: %s. Falling back to in-place execution.", e)

        return self

    def sync_to_workspace(self, changed_files: list[str]) -> None:
        """成功后把隔离 worktree 里的变更文件同步回主工作区（含删除主工作区里已不存在的文件）。"""
        if not self.is_active:
            return

        logger.info("Syncing changed files from worktree back to primary workspace...")
        for rel_path in changed_files:
            if not rel_path:
                continue
            src_file = self.worktree_dir / rel_path
            dest_file = self.workspace / rel_path
            if src_file.exists() and src_file.is_file():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dest_file)
                logger.info("Copied back: %s", rel_path)
            elif not src_file.exists() and dest_file.exists():
                dest_file.unlink(missing_ok=True)
                logger.info("Deleted from primary: %s", rel_path)

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if not self.is_active:
            return

        try:
            logger.info("Cleaning up Git worktree and branch...")
            # 1. 移除 git worktree——Windows 上快速并行移除会撞文件句柄锁，重试几次。
            removed = False
            for attempt in range(3):
                completed = subprocess.run(
                    ["git", "worktree", "remove", "--force", str(self.worktree_dir)],
                    cwd=self.workspace,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if completed.returncode == 0:
                    removed = True
                    break
                time.sleep(0.2 * (attempt + 1))
            if not removed:
                # 重试仍失败：强删目录 + prune 反注册死 worktree，避免 worktree 注册表/分支泄漏。
                logger.warning(
                    "git worktree remove failed after retries (%s); forcing dir removal + prune.", completed.stderr
                )
                if self.worktree_dir.exists():
                    shutil.rmtree(self.worktree_dir, ignore_errors=True)
                subprocess.run(
                    ["git", "worktree", "prune"],
                    cwd=self.workspace,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

            # 2. 删除临时分支（worktree 已移除/反注册后才能删）。
            completed_branch = subprocess.run(
                ["git", "branch", "-D", self.branch_name],
                cwd=self.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed_branch.returncode != 0:
                logger.warning("Failed to delete temporary branch cleanly: %s", completed_branch.stderr)

            # 3. 目录残留兜底。
            if self.worktree_dir.exists():
                shutil.rmtree(self.worktree_dir, ignore_errors=True)

        except Exception as e:
            logger.warning("Error during Git worktree cleanup: %s", e)
