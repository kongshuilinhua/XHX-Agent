from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir


class CheckpointFile(BaseModel):
    path: str
    sha256: str
    size_bytes: int


class Checkpoint(BaseModel):
    id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    kind: str
    files: list[CheckpointFile] = []
    git_head: str | None = None
    git_dirty: bool | None = None
    note: str = ""


def create_checkpoint(workspace: Path, run_id: str, changed_files: list[str] | None = None) -> Checkpoint:
    ensure_xhx_dirs(workspace)
    files = [_checkpoint_file(workspace, path) for path in sorted(set(changed_files or [])) if (workspace / path).is_file()]
    checkpoint = Checkpoint(
        id=f"checkpoint-{run_id}",
        kind="pre_verification",
        files=files,
        git_head=_git_head(workspace),
        git_dirty=_git_dirty(workspace),
        note="Snapshot before verification and possible repair.",
    )
    path = checkpoint_path(workspace, run_id)
    path.write_text(checkpoint.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return checkpoint


def checkpoint_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "checkpoints" / f"{run_id}.json"


def _checkpoint_file(workspace: Path, path: str) -> CheckpointFile:
    target = workspace / path
    data = target.read_bytes()
    return CheckpointFile(
        path=path.replace("\\", "/"),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _git_head(workspace: Path) -> str | None:
    completed = _run_git(workspace, "rev-parse", "HEAD")
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def _git_dirty(workspace: Path) -> bool | None:
    completed = _run_git(workspace, "status", "--porcelain")
    if completed.returncode != 0:
        return None
    return bool(completed.stdout.strip())


def _run_git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={workspace.as_posix()}", *args],
        cwd=workspace,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        shell=False,
        timeout=10,
    )
