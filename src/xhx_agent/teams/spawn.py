"""队友生成：支持 in-process 和 tmux pane 两种后端，自动降级。"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import threading
from dataclasses import dataclass
from typing import Any

from xhx_agent.teams.models import BackendType
from xhx_agent.teams.progress import TeammateProgress

log = logging.getLogger(__name__)


def _tmux_available() -> bool:
    """检测 tmux 是否可用且当前进程在 tmux 会话内。"""
    if shutil.which("tmux") is None:
        return False
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "#{session_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (subprocess.SubprocessError, OSError):
        return False


def detect_backend(teammate_mode: str | None = None, is_interactive: bool = True) -> BackendType:
    """探测最佳可用后端。优先 tmux（如可用），否则降级到 in-process。"""
    if teammate_mode == "in-process":
        return BackendType.IN_PROCESS
    if teammate_mode == "tmux":
        if _tmux_available():
            return BackendType.TMUX
        log.warning("tmux requested but not available; falling back to in-process")
        return BackendType.IN_PROCESS

    if is_interactive and _tmux_available():
        return BackendType.TMUX
    return BackendType.IN_PROCESS


@dataclass
class InProcessTeammateHandle:
    """In-process 队友的运行句柄。"""

    name: str = ""
    progress: TeammateProgress | None = None
    _task: asyncio.Task[str] | None = None
    _done_event: threading.Event | None = None

    @property
    def done(self) -> bool:
        if self._task:
            return self._task.done()
        return self._done_event.is_set() if self._done_event else True

    @property
    def result(self) -> str | None:
        if self._task and self._task.done():
            try:
                return self._task.result()
            except (asyncio.CancelledError, Exception):
                return None
        return None

    def cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()


def spawn_inprocess_teammate(
    agent: Any,
    prompt: str,
    name: str = "",
    team_name: str = "",
) -> InProcessTeammateHandle:
    """在进程中生成一个队友 Agent。"""
    progress = TeammateProgress(name=name, team_name=team_name)

    async def _run() -> str:
        try:
            result = await agent.run_to_completion(prompt)
            progress.status = "completed"
            return result or ""
        except asyncio.CancelledError:
            progress.status = "stopped"
            return ""
        except Exception:
            progress.status = "failed"
            return ""

    task = asyncio.ensure_future(_run())
    handle = InProcessTeammateHandle(name=name, progress=progress, _task=task)
    return handle


# ---------------------------------------------------------------------------
# Tmux pane 后端
# ---------------------------------------------------------------------------


@dataclass
class TmuxTeammateHandle:
    """Tmux pane 队友的运行句柄。"""

    name: str = ""
    pane_id: str = ""
    progress: TeammateProgress | None = None
    _poll_task: asyncio.Task[str] | None = None

    @property
    def done(self) -> bool:
        if self._poll_task:
            return self._poll_task.done()
        return self._is_pane_dead()

    @property
    def result(self) -> str | None:
        if self._poll_task and self._poll_task.done():
            try:
                return self._poll_task.result()
            except (asyncio.CancelledError, Exception):
                return None
        return None

    def cancel(self) -> None:
        if self.pane_id:
            try:
                subprocess.run(
                    ["tmux", "send-keys", "-t", self.pane_id, "C-c", ""],
                    capture_output=True,
                    timeout=5,
                )
            except (subprocess.SubprocessError, OSError):
                pass
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()

    def _is_pane_dead(self) -> bool:
        if not self.pane_id:
            return True
        try:
            result = subprocess.run(
                ["tmux", "list-panes", "-F", "#{pane_id}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return self.pane_id not in result.stdout
        except (subprocess.SubprocessError, OSError):
            return True

    def capture_output(self, lines: int = 50) -> str:
        if not self.pane_id:
            return ""
        try:
            result = subprocess.run(
                ["tmux", "capture-pane", "-t", self.pane_id, "-p", "-S", f"-{lines}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.stdout if result.returncode == 0 else ""
        except (subprocess.SubprocessError, OSError):
            return ""


def spawn_tmux_teammate(
    command: str,
    work_dir: str,
    name: str = "",
    team_name: str = "",
) -> TmuxTeammateHandle:
    """在新 tmux pane 中生成队友进程。"""
    progress = TeammateProgress(name=name, team_name=team_name)

    try:
        result = subprocess.run(
            [
                "tmux",
                "split-window",
                "-h",
                "-P",
                "-F",
                "#{pane_id}",
                "-c",
                work_dir,
                command,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            raise RuntimeError(f"tmux split-window failed: {result.stderr}")
        pane_id = result.stdout.strip()
    except (subprocess.SubprocessError, OSError) as e:
        raise RuntimeError(f"Failed to spawn tmux teammate: {e}")

    handle = TmuxTeammateHandle(name=name, pane_id=pane_id, progress=progress)

    async def _poll() -> str:
        while not handle._is_pane_dead():
            await asyncio.sleep(2)
        progress.status = "completed"
        return handle.capture_output(100)

    handle._poll_task = asyncio.ensure_future(_poll())
    return handle
