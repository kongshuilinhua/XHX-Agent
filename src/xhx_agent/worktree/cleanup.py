"""Worktree 三层过期后台清理。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


async def start_stale_cleanup_task(
    manager: Any,
    interval: int = 300,
    cutoff_hours: int = 24,
    **kwargs: Any,
) -> None:
    """启动后台任务，定期清理 stale worktree。

    三层清理策略：
    1. 目录已不存在的幽灵条目 → 立即移除
    2. 超过 cutoff_hours 未活跃 → 调用 manager.cleanup_stale 移除
    3. 无变更的 worktree → 由 auto_cleanup 在退出时处理（不在此层）
    """

    async def _loop() -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                removed = await manager.cleanup_stale(cutoff_hours=cutoff_hours)
                if removed:
                    log.info("Stale worktree cleanup: removed %d entries", removed)
            except Exception:
                log.debug("Stale worktree cleanup pass failed", exc_info=True)

    asyncio.create_task(_loop())
