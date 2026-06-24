"""WorktreeManager — Git worktree 全生命周期管理。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from xhx_agent.worktree._git import run_git
from xhx_agent.worktree.slug import validate_slug

if TYPE_CHECKING:
    from xhx_agent.worktree.changes import CleanupResult

log = logging.getLogger(__name__)

SESSION_FILE = "worktree_sessions.json"


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
        self._lock = asyncio.Lock()

    async def create(self, name: str, base_ref: str = "HEAD") -> WorktreeHandle:
        """创建新 worktree（并发安全）。"""
        import secrets

        from xhx_agent.worktree.integration import generate_worktree_name

        slug = self._validated_slug(name) if name else generate_worktree_name()
        branch = f"xhx-wt-{slug}-{secrets.token_hex(4)}"
        wt_dir = self.worktrees_dir / slug

        async with self._lock:
            if slug in self._active:
                return self._active[slug]

            self.worktrees_dir.mkdir(parents=True, exist_ok=True)

            result = run_git(
                ["worktree", "add", "-b", branch, str(wt_dir)],
                cwd=self.project_root,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to create worktree: {result.stderr}")

            self._post_create_setup(wt_dir)

            handle = WorktreeHandle(
                path=wt_dir,
                branch=branch,
                name=slug,
                head_commit=self._head_sha(wt_dir),
            )
            self._active[slug] = handle
            self._persist_sessions()

        return handle

    async def enter(self, name: str) -> WorktreeHandle:
        """进入已有 worktree。"""
        wt_dir = self.worktrees_dir / name
        if not wt_dir.exists():
            raise FileNotFoundError(f"Worktree not found: {name}")
        handle = WorktreeHandle(path=wt_dir, branch="", name=name, head_commit=self._head_sha(wt_dir))
        async with self._lock:
            self._active[name] = handle
        return handle

    async def exit(self, name: str, action: str = "keep", discard_changes: bool = False) -> None:
        """退出 worktree。"""
        async with self._lock:
            if name not in self._active:
                return
            handle = self._active.pop(name)

        if action == "remove":
            try:
                run_git(["worktree", "remove", "--force", str(handle.path)], cwd=self.project_root)
                if handle.branch:
                    run_git(["branch", "-D", handle.branch], cwd=self.project_root)
            except Exception:
                pass

        self._persist_sessions()

    async def auto_cleanup(self, name: str, head_commit: str) -> CleanupResult:
        """自动清理无变更的 worktree（fail-closed）。"""
        from xhx_agent.worktree.changes import CleanupResult, has_worktree_changes

        async with self._lock:
            if name in self._active:
                handle = self._active[name]
                if not has_worktree_changes(str(handle.path), head_commit):
                    self._active.pop(name, None)
                    self._remove_worktree_dir(handle)
                    self._persist_sessions()
                    return CleanupResult(kept=False)
                return CleanupResult(kept=True, path=str(handle.path), branch=handle.branch)
        return CleanupResult(kept=False)

    async def cleanup_stale(self, cutoff_hours: int = 24) -> int:
        """清理超过 cutoff_hours 未活跃的 worktree。"""
        import time

        cutoff = time.time() - cutoff_hours * 3600
        removed = 0
        async with self._lock:
            stale_names = []
            for name, handle in self._active.items():
                if not handle.path.exists():
                    stale_names.append(name)
                    continue
                try:
                    mtime = handle.path.stat().st_mtime
                    if mtime < cutoff:
                        stale_names.append(name)
                except OSError:
                    stale_names.append(name)
            for n in stale_names:
                removed_handle = self._active.pop(n, None)
                if removed_handle is not None:
                    self._remove_worktree_dir(removed_handle)
                removed += 1
            if removed:
                self._persist_sessions()
        return removed

    def _head_sha(self, wt_path: Path) -> str:
        """用 git rev-parse 获取 HEAD SHA（比读文件更稳妥）。"""
        try:
            result = run_git(["rev-parse", "HEAD"], cwd=wt_path)
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            pass
        # fast recovery: 纯文件系统读 .git 指针链
        try:
            git_dir = wt_path / ".git"
            if git_dir.is_file():
                content = git_dir.read_text().strip()
                if content.startswith("gitdir:"):
                    real_git = Path(content.split(":", 1)[1].strip())
                    if not real_git.is_absolute():
                        real_git = (wt_path / real_git).resolve()
                    head_file = real_git / "HEAD"
                    if head_file.exists():
                        head_content = head_file.read_text().strip()
                        if head_content.startswith("ref:"):
                            ref_path = real_git / head_content.split(":", 1)[1].strip()
                            if ref_path.exists():
                                return ref_path.read_text().strip()
                        return head_content
        except Exception:
            pass
        return ""

    def list_worktrees(self) -> list[WorktreeHandle]:
        return list(self._active.values())

    def restore_session(self) -> WorktreeHandle | None:
        """从磁盘恢复上次会话的 worktree。"""
        session_path = self.worktrees_dir / SESSION_FILE
        if not session_path.exists():
            return None
        try:
            data = json.loads(session_path.read_text(encoding="utf-8"))
            entries = data if isinstance(data, list) else data.get("sessions", [])
            last_handle: WorktreeHandle | None = None
            for entry in entries:
                wt_dir = Path(entry["path"])
                if not wt_dir.exists():
                    continue
                handle = WorktreeHandle(
                    path=wt_dir,
                    branch=entry.get("branch", ""),
                    name=entry["name"],
                    head_commit=self._head_sha(wt_dir),
                )
                self._active[handle.name] = handle
                last_handle = handle
            return last_handle
        except Exception as e:
            log.warning("Failed to restore worktree session: %s", e)
            return None

    def _persist_sessions(self) -> None:
        """将当前活跃 worktree 信息持久化到磁盘。"""
        session_path = self.worktrees_dir / SESSION_FILE
        try:
            self.worktrees_dir.mkdir(parents=True, exist_ok=True)
            entries = [{"name": h.name, "path": str(h.path), "branch": h.branch} for h in self._active.values()]
            session_path.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        except Exception as e:
            log.warning("Failed to persist worktree sessions: %s", e)

    def _post_create_setup(self, wt_dir: Path) -> None:
        """创建后设置：复制 git hooks + 创建大目录软链接。"""
        # 复制 git hooks
        src_hooks = self.project_root / ".git" / "hooks"
        if src_hooks.is_dir():
            # worktree 的 .git 是一个指向主仓库的文件，hooks 在主仓库共享
            # 但自定义 hooks 可能在 .xhx/hooks，复制过去
            xhx_hooks = self.project_root / ".xhx" / "hooks"
            if xhx_hooks.is_dir():
                dst_hooks = wt_dir / ".xhx" / "hooks"
                dst_hooks.mkdir(parents=True, exist_ok=True)
                for hook_file in xhx_hooks.iterdir():
                    if hook_file.is_file():
                        shutil.copy2(hook_file, dst_hooks / hook_file.name)

        # 大目录软链接（node_modules, .venv 等）
        for dir_name in self.symlink_directories:
            src = self.project_root / dir_name
            dst = wt_dir / dir_name
            if src.exists() and not dst.exists():
                try:
                    os.symlink(src, dst)
                except OSError as e:
                    log.debug("Symlink %s -> %s failed: %s", dst, src, e)

        # 复制 .worktreeinclude（如果存在）
        include_file = self.project_root / ".worktreeinclude"
        if include_file.is_file():
            shutil.copy2(include_file, wt_dir / ".worktreeinclude")

    def _remove_worktree_dir(self, handle: WorktreeHandle) -> None:
        try:
            run_git(["worktree", "remove", "--force", str(handle.path)], cwd=self.project_root)
            if handle.branch:
                run_git(["branch", "-D", handle.branch], cwd=self.project_root)
        except Exception:
            pass

    @staticmethod
    def _validated_slug(name: str) -> str:
        from xhx_agent.worktree.slug import flatten_slug

        error = validate_slug(name)
        if error:
            raise ValueError(f"Invalid worktree name: {error}")
        return flatten_slug(name)


class WorktreeHandle:
    def __init__(self, path: Path, branch: str, name: str, head_commit: str) -> None:
        self.path = path
        self.worktree_path = str(path)
        self.branch = branch
        self.name = name
        self.head_commit = head_commit
