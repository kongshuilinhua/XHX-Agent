from __future__ import annotations

import time
from collections.abc import Callable
from uuid import uuid4

CancelCheck = Callable[[], bool]


def new_run_id(prefix: str) -> str:
    """Return a process-wide unique run id.

    A bare second-resolution timestamp collides when two runs start in the same second,
    which silently corrupts trace/evidence files and causes worktree branch-name clashes.
    The short uuid suffix makes every run id unique while staying human-readable.
    """

    return f"{prefix}-{int(time.time())}-{uuid4().hex[:8]}"


def cancel_requested(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:
        return False
