from __future__ import annotations

import os
from pathlib import Path

XHX_DIR = ".xhx"


def resolve_workspace(path: Path | None = None) -> Path:
    return (path or Path.cwd()).resolve()


def xhx_dir(workspace: Path) -> Path:
    return workspace / XHX_DIR


def global_xhx_dir() -> Path:
    """用户级全局 .xhx 目录：跨项目共享的模型/profile 配置兜底位置。

    优先用 XHX_HOME 环境变量（便于测试与自定义），否则落在 ~/.xhx。
    与项目级 .xhx 的区别：这里只放 config.json / profiles.json 这类“配一次、到处用”
    的配置，不放 traces/sessions 等跟随单个项目的运行时状态。
    """
    env = os.environ.get("XHX_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / XHX_DIR


def ensure_xhx_dirs(workspace: Path) -> None:
    root = xhx_dir(workspace)
    for name in ("sessions", "traces", "evidence", "context", "logbook", "checkpoints", "repo", "skills"):
        (root / name).mkdir(parents=True, exist_ok=True)
