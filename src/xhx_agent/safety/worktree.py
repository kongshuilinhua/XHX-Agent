from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def is_git_repo(workspace: Path) -> bool:
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
        """Copy changed files from isolated worktree back to primary workspace on success."""
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
            # 1. Remove git worktree
            completed = subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.worktree_dir)],
                cwd=self.workspace,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                logger.warning("Failed to remove Git worktree cleanly: %s", completed.stderr)

            # 2. Delete branch
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

            # 3. Clean up directory if still exists
            if self.worktree_dir.exists():
                shutil.rmtree(self.worktree_dir, ignore_errors=True)

        except Exception as e:
            logger.warning("Error during Git worktree cleanup: %s", e)
