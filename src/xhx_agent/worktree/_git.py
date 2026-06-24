"""Git 安全壳：所有 worktree 模块共用的 git 执行入口。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_SAFE_ENV = {"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "", "GIT_SSH_COMMAND": "ssh -oBatchMode=yes"}


def run_git(args: list[str], cwd: str | Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_SAFE_ENV}
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=30,
        stdin=subprocess.DEVNULL,
        env=env,
    )
