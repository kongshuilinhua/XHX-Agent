from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import ensure_xhx_dirs, xhx_dir

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class SessionEntry(BaseModel):
    """A persisted summary of one completed run, used to resume a session."""

    run_id: str
    task: str
    status: str
    verification: str = "not_executed"
    changed_files: list[str] = []
    summary_path: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def session_history_path(workspace: Path) -> Path:
    return xhx_dir(workspace) / "sessions" / "history.jsonl"


def record_session(workspace: Path, task: str, result: RunResult) -> SessionEntry:
    """Append a one-line summary of ``result`` to the session history."""

    ensure_xhx_dirs(workspace)
    entry = SessionEntry(
        run_id=result.run_id,
        task=task,
        status=result.status,
        verification=result.verification,
        changed_files=list(result.changed_files),
        summary_path=result.summary_path,
    )
    with session_history_path(workspace).open("a", encoding="utf-8") as handle:
        handle.write(entry.model_dump_json() + "\n")
    return entry


def list_sessions(workspace: Path) -> list[SessionEntry]:
    """Return all recorded session entries in chronological order."""

    path = session_history_path(workspace)
    if not path.exists():
        return []
    return [
        SessionEntry.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_latest_session(workspace: Path) -> SessionEntry | None:
    """Return the most recently recorded session entry, or None if there is none."""

    entries = list_sessions(workspace)
    return entries[-1] if entries else None


def load_session(workspace: Path, run_id: str) -> SessionEntry | None:
    """Return the most recent recorded session with ``run_id``, or None."""

    for entry in reversed(list_sessions(workspace)):
        if entry.run_id == run_id:
            return entry
    return None


def format_follow_up(entry: SessionEntry) -> str:
    """Render a previous run summary as follow-up context for a continued task."""

    changed = ", ".join(entry.changed_files) or "none"
    return (
        "Continuing the previous session. Previous run context:\n"
        f"- run_id: {entry.run_id}\n"
        f"- task: {entry.task}\n"
        f"- status: {entry.status}\n"
        f"- verification: {entry.verification}\n"
        f"- changed_files: {changed}\n"
        "Use the previous context only when it is relevant. "
        "Keep normal safety, apply_patch, and verification rules."
    )
