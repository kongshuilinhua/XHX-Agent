from __future__ import annotations

from collections.abc import Callable
from typing import Any


class HooksManager:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = {
            "before_plan": [],
            "before_patch": [],
            "after_verify": [],
            "before_summary": [],
        }

    def clear(self) -> None:
        """Clear all registered hooks. Useful for testing."""
        for stage in self._hooks:
            self._hooks[stage].clear()

    def reset(self) -> None:
        """Alias for clear. Resets the manager state for tests."""
        self.clear()

    def register(self, stage: str, callback: Callable[..., Any]) -> None:
        if stage in self._hooks:
            self._hooks[stage].append(callback)
        else:
            raise ValueError(f"Invalid lifecycle stage: {stage}")

    def trigger(self, stage: str, *args: Any, **kwargs: Any) -> None:
        if stage in self._hooks:
            for callback in self._hooks[stage]:
                try:
                    callback(*args, **kwargs)
                except Exception as e:
                    # Print and absorb hook exception to maintain app stability
                    print(f"Error in hook '{stage}' callback {callback}: {e}")


# Global instance of hooks manager
hooks_manager = HooksManager()
