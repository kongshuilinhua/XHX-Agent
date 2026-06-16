"""In-Process 队友生成。来源：mewcode teams/spawn_inprocess.py。

仅支持 in-process 后端（Windows 兼容）。"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any

from xhx_agent.teams.models import BackendType, TeammateInfo
from xhx_agent.teams.progress import TeammateProgress


def detect_backend(teammate_mode: str | None = None, is_interactive: bool = True) -> BackendType:
    """探测可用后端。XHX-Agent 仅支持 in-process。"""
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
    """在进程中生成一个队友 Agent。

    返回 InProcessTeammateHandle，可通过 handle.done / handle.result 获取状态。
    """
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
