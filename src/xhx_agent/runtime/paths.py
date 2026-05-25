from __future__ import annotations

from pathlib import Path


XHX_DIR = ".xhx"


def resolve_workspace(path: Path | None = None) -> Path:
    return (path or Path.cwd()).resolve()


def xhx_dir(workspace: Path) -> Path:
    return workspace / XHX_DIR


def ensure_xhx_dirs(workspace: Path) -> None:
    root = xhx_dir(workspace)
    for name in ("sessions", "traces", "evidence", "logbook", "checkpoints", "skills"):
        (root / name).mkdir(parents=True, exist_ok=True)
