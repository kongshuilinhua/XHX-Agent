from __future__ import annotations

from pathlib import Path


def read_file(workspace: Path, path: str, max_bytes: int = 200_000, start_line: int = 1, max_lines: int = 200) -> str:
    target = _resolve_inside(workspace, path)
    if target.stat().st_size > max_bytes:
        raise ValueError(f"File exceeds max bytes: {path}")
    lines = target.read_text(encoding="utf-8").splitlines()
    start = max(start_line - 1, 0)
    return "\n".join(lines[start : start + max_lines])


def _resolve_inside(workspace: Path, path: str) -> Path:
    target = Path(workspace / path).resolve()
    if not target.is_file():
        raise FileNotFoundError(path)
    return target
