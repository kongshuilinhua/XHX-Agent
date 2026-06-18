"""Worktree 自动清理任务。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


async def start_stale_cleanup_task(
    manager: Any,
    interval: int = 300,
    cutoff_hours: int = 24,
    **kwargs: Any,
) -> None:
    """启动后台任务，定期清理 stale worktree。"""

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                for wt in manager.list_worktrees():
                    if not Path(wt.path).exists():
                        manager._active.pop(wt.name, None)
            except Exception:
                pass

    asyncio.create_task(_loop())
