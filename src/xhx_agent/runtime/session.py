from __future__ import annotations

import json
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
    transcript_path: str | None = None
    mode: str = ""
    # Stable id shared by every turn of one interactive console conversation. Empty for one-shot
    # CLI runs (each stands alone). Lets the resume picker collapse a multi-turn dialogue to one entry.
    conversation_id: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


def session_history_path(workspace: Path) -> Path:
    return xhx_dir(workspace) / "sessions" / "history.jsonl"


def transcript_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "sessions" / f"{run_id}.json"


def save_transcript(workspace: Path, run_id: str, messages: list[dict]) -> str:
    """落盘整段消息历史，返回相对 workspace 的 POSIX 路径（写进索引/RunResult）。"""
    ensure_xhx_dirs(workspace)
    path = transcript_path(workspace, run_id)
    path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.relative_to(workspace).as_posix()


def load_transcript_messages(workspace: Path, rel_path: str | None) -> list[dict] | None:
    """按相对路径读回整段历史；缺文件/空路径返回 None（让上层回退摘要续接）。"""
    if not rel_path:
        return None
    path = workspace / rel_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def record_session(workspace: Path, task: str, result: RunResult, conversation_id: str = "") -> SessionEntry:
    """Append a one-line summary of ``result`` to the session history (+ persist full transcript when present).

    ``conversation_id`` ties together the turns of one interactive console conversation so the resume
    picker can collapse them into a single entry; leave empty for standalone one-shot runs.
    """

    ensure_xhx_dirs(workspace)
    rel_transcript = getattr(result, "transcript_path", None)
    messages = getattr(result, "messages", None)
    if rel_transcript is None and messages:
        rel_transcript = save_transcript(workspace, result.run_id, messages)
    entry = SessionEntry(
        run_id=result.run_id,
        task=task,
        status=result.status,
        verification=result.verification,
        changed_files=list(result.changed_files),
        summary_path=result.summary_path,
        transcript_path=rel_transcript,
        mode=getattr(result, "mode", "") or "",
        conversation_id=conversation_id,
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
        SessionEntry.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def list_conversations(workspace: Path) -> list[SessionEntry]:
    """Return one entry per conversation (the latest turn), collapsing multi-turn dialogues.

    Turns of one interactive console conversation share a ``conversation_id`` and collapse to their
    most recent entry — whose transcript is the full conversation. Entries without a conversation_id
    are standalone (keyed by run_id), so one-shot CLI runs each stand on their own. Ordered
    chronologically by the kept entry's ``created_at``.
    """

    latest: dict[str, SessionEntry] = {}
    first_task: dict[str, str] = {}
    for entry in list_sessions(workspace):
        key = entry.conversation_id or f"run:{entry.run_id}"
        first_task.setdefault(key, entry.task)  # opening task = the conversation's topic/title
        latest[key] = entry  # later turns overwrite → keep the most recent, fullest transcript
    # Title each conversation by its opening task, but keep the latest run_id/transcript so resume
    # restores the full dialogue.
    titled = [entry.model_copy(update={"task": first_task[key]}) for key, entry in latest.items()]
    return sorted(titled, key=lambda entry: entry.created_at)


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
