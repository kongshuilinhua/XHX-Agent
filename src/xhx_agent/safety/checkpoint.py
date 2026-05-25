from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

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


class CheckpointRestoreFile(BaseModel):
    path: str
    checkpoint_sha256: str
    current_sha256: str | None = None
    status: Literal["unchanged", "changed", "missing"]
    note: str


class CheckpointRestorePlan(BaseModel):
    id: str
    checkpoint_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    kind: str = "manual_restore_plan"
    can_auto_restore: bool = False
    files: list[CheckpointRestoreFile] = []
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


def create_restore_plan(workspace: Path, run_id: str, checkpoint: Checkpoint) -> CheckpointRestorePlan:
    """Write a read-only restore plan without modifying repository files.

    v0.2 checkpoints intentionally store metadata only. This plan records what
    changed after the checkpoint and makes the recovery boundary explicit.
    """

    ensure_xhx_dirs(workspace)
    plan = CheckpointRestorePlan(
        id=f"restore-plan-{run_id}",
        checkpoint_id=checkpoint.id,
        files=[_restore_file(workspace, item) for item in checkpoint.files],
        git_head=checkpoint.git_head,
        git_dirty=_git_dirty(workspace),
        note=(
            "Read-only plan only. v0.2 checkpoints store hashes and sizes, not original file contents; "
            "automatic rollback is intentionally not executed."
        ),
    )
    path = restore_plan_path(workspace, run_id)
    path.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return plan


def checkpoint_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "checkpoints" / f"{run_id}.json"


def restore_plan_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "checkpoints" / f"{run_id}-restore-plan.json"


def _checkpoint_file(workspace: Path, path: str) -> CheckpointFile:
    target = workspace / path
    data = target.read_bytes()
    return CheckpointFile(
        path=path.replace("\\", "/"),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
    )


def _restore_file(workspace: Path, checkpoint_file: CheckpointFile) -> CheckpointRestoreFile:
    target = workspace / checkpoint_file.path
    if not target.exists():
        return CheckpointRestoreFile(
            path=checkpoint_file.path,
            checkpoint_sha256=checkpoint_file.sha256,
            status="missing",
            note="File is missing after checkpoint. Inspect the report and restore manually if needed.",
        )
    current = _file_sha256(target)
    status: Literal["unchanged", "changed"] = "unchanged" if current == checkpoint_file.sha256 else "changed"
    note = (
        "File matches checkpoint metadata."
        if status == "unchanged"
        else "File changed after checkpoint. Inspect git diff or restore manually; no automatic rollback was run."
    )
    return CheckpointRestoreFile(
        path=checkpoint_file.path,
        checkpoint_sha256=checkpoint_file.sha256,
        current_sha256=current,
        status=status,
        note=note,
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
