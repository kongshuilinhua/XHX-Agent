"""WorktreeManager — Git worktree 全生命周期管理。"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhx_agent.worktree.changes import CleanupResult

log = logging.getLogger(__name__)


class WorktreeManager:
    """管理多个 git worktree 的创建/进入/退出/清理。"""

    def __init__(
        self,
        project_root: str | Path = "",
        repo_root: str | Path = "",
        symlink_directories: list[str] | None = None,
    ) -> None:
        root = project_root or repo_root or "."
        self.project_root = Path(root).resolve()
        self.worktrees_dir = self.project_root / ".xhx" / "worktrees"
        self.symlink_directories = symlink_directories or []
        self._active: dict[str, WorktreeHandle] = {}

    async def create(self, name: str, base_ref: str = "HEAD") -> WorktreeHandle:
        """创建新 worktree。"""
        import secrets

        from xhx_agent.worktree.integration import generate_worktree_name
        from xhx_agent.worktree.slug import flatten_slug

        slug = flatten_slug(name) if name else generate_worktree_name()
        branch = f"xhx-wt-{slug}-{secrets.token_hex(4)}"
        wt_dir = self.worktrees_dir / slug

        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                ["git", "worktree", "add", "-b", branch, str(wt_dir)],
                cwd=self.project_root,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to create worktree: {e.stderr}")

        handle = WorktreeHandle(
            path=wt_dir,
            branch=branch,
            name=slug,
            head_commit=self._head_sha(wt_dir),
        )
        self._active[slug] = handle
        return handle

    async def enter(self, name: str) -> WorktreeHandle:
        """进入已有 worktree。"""
        wt_dir = self.worktrees_dir / name
        if not wt_dir.exists():
            raise FileNotFoundError(f"Worktree not found: {name}")
        handle = WorktreeHandle(path=wt_dir, branch="", name=name, head_commit=self._head_sha(wt_dir))
        self._active[name] = handle
        return handle

    async def exit(self, name: str, action: str = "keep", discard_changes: bool = False) -> None:
        """退出 worktree。"""
        if name not in self._active:
            return
        handle = self._active.pop(name)
        if action == "remove":
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(handle.path)],
                    cwd=self.project_root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if handle.branch:
                    subprocess.run(
                        ["git", "branch", "-D", handle.branch],
                        cwd=self.project_root,
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
            except Exception:
                pass

    async def auto_cleanup(self, name: str, head_commit: str) -> CleanupResult:
        """自动清理无变更的 worktree。"""
        from xhx_agent.worktree.changes import CleanupResult, has_worktree_changes

        if name in self._active:
            handle = self._active[name]
            if not has_worktree_changes(str(handle.path), head_commit):
                await self.exit(name, action="remove")
                return CleanupResult(kept=False)
            return CleanupResult(kept=True, path=str(handle.path), branch=handle.branch)
        return CleanupResult(kept=False)

    def _head_sha(self, wt_path: Path) -> str:
        try:
            head_file = wt_path / ".git" / "HEAD"
            if head_file.exists():
                return head_file.read_text().strip()
        except Exception:
            pass
        return ""

    def list_worktrees(self) -> list[WorktreeHandle]:
        return list(self._active.values())

    def restore_session(self) -> WorktreeHandle | None:
        """恢复上次会话的 worktree。"""
        return None


class WorktreeHandle:
    def __init__(self, path: Path, branch: str, name: str, head_commit: str) -> None:
        self.path = path
        self.worktree_path = str(path)
        self.branch = branch
        self.name = name
        self.head_commit = head_commit
