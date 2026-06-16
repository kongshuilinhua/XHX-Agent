"""Thread-safe file content cache.

Provides a simple in-memory cache for file contents, protected by a mutex,
so that concurrent sub-agents reading the same file don't trigger redundant
disk I/O.  The cache is intentionally simple — no TTL, no LRU — because
agent-run lifetimes are short and stale reads are unlikely.  Callers that
mutate files are expected to invalidate the affected paths explicitly.
"""

from __future__ import annotations

import threading


class FileCache:
    """Thread-safe string-keyed cache for file contents."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def get(self, path: str) -> str | None:
        """Return cached content for *path*, or *None*."""
        with self._lock:
            return self._store.get(path)

    def put(self, path: str, content: str) -> None:
        """Store *content* under *path*."""
        with self._lock:
            self._store[path] = content

    def invalidate(self, path: str) -> None:
        """Remove *path* from the cache (no-op if absent)."""
        with self._lock:
            self._store.pop(path, None)

    def clear(self) -> None:
        """Drop every cached entry."""
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)

    def __contains__(self, path: str) -> bool:
        with self._lock:
            return path in self._store


# ------------------------------------------------------------------
# module-level singleton — importers that don't need custom lifecycle
# can just use ``file_cache.get(...)`` / ``file_cache.put(...)``.
# ------------------------------------------------------------------

file_cache = FileCache()
