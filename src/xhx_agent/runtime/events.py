from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Callable

from pydantic import BaseModel, Field


class RuntimeEvent(BaseModel):
    type: str
    message: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    payload: dict[str, Any] = Field(default_factory=dict)


EventCallback = Callable[[RuntimeEvent], None]


def emit_event(callback: EventCallback | None, event_type: str, message: str, **payload: Any) -> None:
    if callback is None:
        return
    callback(RuntimeEvent(type=event_type, message=message, payload=payload))
