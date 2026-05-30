from __future__ import annotations

from collections.abc import Callable

CancelCheck = Callable[[], bool]


def cancel_requested(cancel_check: CancelCheck | None) -> bool:
    if cancel_check is None:
        return False
    try:
        return bool(cancel_check())
    except Exception:
        return False
