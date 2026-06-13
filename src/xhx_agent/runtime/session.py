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
    view_path: str | None = None
    turn_count: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
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


def view_log_path(workspace: Path, run_id: str) -> Path:
    return xhx_dir(workspace) / "sessions" / f"{run_id}.view.json"


def save_view_log(workspace: Path, run_id: str, lines: list[str]) -> str:
    """落盘界面日志，返回相对 workspace 的 POSIX 路径。"""
    ensure_xhx_dirs(workspace)
    path = view_log_path(workspace, run_id)
    path.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.relative_to(workspace).as_posix()


def load_view_log(workspace: Path, rel_path: str | None) -> list[str] | None:
    """按相对路径读回界面日志；缺文件/空路径返回 None。"""
    if not rel_path:
        return None
    path = workspace / rel_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def record_session(
    workspace: Path,
    task: str,
    result: RunResult,
    conversation_id: str = "",
    view_path: str | None = None,
    turn_count: int = 0,
) -> SessionEntry:
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
        view_path=view_path,
        turn_count=turn_count,
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


def resolve_run_id(entries: list[SessionEntry], token: str) -> tuple[str | None, list[str]]:
    """Try to resolve a token to a single run_id in the entries.

    Returns (resolved_run_id, candidates_if_ambiguous).
    """
    # Exact match first
    for entry in entries:
        if entry.run_id == token:
            return token, []

    # Prefix or suffix matches
    cands = []
    for entry in entries:
        if (entry.run_id.startswith(token) or entry.run_id.endswith(token)) and entry.run_id not in cands:
            cands.append(entry.run_id)

    if len(cands) == 1:
        return cands[0], []
    elif len(cands) == 0:
        return None, []
    else:
        return None, sorted(cands)


def _relative_time(t_str: str | None, created_at: str | None, now: datetime) -> str:
    """Calculate relative time string."""
    target_str = t_str or created_at
    try:
        t = datetime.fromisoformat(target_str) if target_str else now
    except Exception:
        try:
            t = datetime.fromisoformat(created_at) if created_at else now
        except Exception:
            t = now

    # Align tzinfo to prevent TypeError: can't subtract offset-naive and offset-aware datetimes
    if t.tzinfo is not None and now.tzinfo is None:
        from datetime import UTC
        now = now.replace(tzinfo=UTC)
    elif t.tzinfo is None and now.tzinfo is not None:
        now = now.replace(tzinfo=None)

    diff = now - t
    diff_sec = diff.total_seconds()
    if diff_sec < 60:  # 含未来时间（diff_sec < 0）一并归为「刚刚」
        return "刚刚"
    elif diff_sec < 3600:
        return f"{int(diff_sec // 60)}分钟前"
    elif diff_sec < 86400:
        return f"{int(diff_sec // 3600)}小时前"
    else:
        return f"{int(diff_sec // 86400)}天前"


def format_session_line(entry: SessionEntry, now: datetime) -> str:
    """Format a session entry to a human readable list item."""
    rel_time = _relative_time(entry.updated_at, entry.created_at, now)
    task_single = " ".join(entry.task.splitlines())
    if len(task_single) > 60:
        task_single = task_single[:60] + "…"

    short_id = entry.run_id[-8:]
    return f"{rel_time} | {entry.status} | 轮{entry.turn_count} | …{short_id} | {task_single}"


def format_session_meta(entry: SessionEntry, now: datetime) -> str:
    """Format a session entry to a dark meta info line."""
    rel_time = _relative_time(entry.updated_at, entry.created_at, now)
    short_id = entry.run_id[-8:]
    return f"{rel_time} · {entry.status} · {entry.turn_count}轮 · …{short_id}"


def prune_legacy_sessions(workspace: Path) -> int:
    """Read history.jsonl, remove entries where view_path is empty/missing, and rewrite it directly."""
    path = session_history_path(workspace)
    if not path.exists():
        return 0

    entries = list_sessions(workspace)
    if not entries:
        return 0

    valid_entries = [e for e in entries if e.view_path]
    removed_count = len(entries) - len(valid_entries)

    if removed_count > 0:
        # Overwrite history.jsonl directly (no backup)
        path.write_text(
            "".join(e.model_dump_json() + "\n" for e in valid_entries),
            encoding="utf-8"
        )

    return removed_count


