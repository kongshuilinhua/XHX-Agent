"""长期记忆 / 跨会话上下文（Phase 6a）：上下文管理的第 ④ 轴（时间轴）。

`store` 负责落盘/索引（`.xhx/memory/` + `MEMORY.md`），`recall` 负责确定性召回并渲染成
可注入 context-pack / 编排器 system prompt 的记忆块。无 LLM 调用、可复现。

`Session` / `SessionManager` / `SessionMeta` 负责会话持久化：消息逐条落盘到
`.xhx/sessions/<id>.jsonl`，元数据写入 `.xhx/sessions/<id>.meta`，支持 resume。
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xhx_agent.memory.auto_memory import MemoryManager
from xhx_agent.memory.extract import parse_memory_candidates, propose_memories
from xhx_agent.memory.recall import find_relevant_memories, recall_memories, render_recalled_memories, render_reminder
from xhx_agent.memory.store import (
    MEMORY_TYPES,
    MemoryRecord,
    delete_memory,
    list_memories,
    memory_dir,
    parse_memory_file,
    slugify,
    write_memory,
)

if TYPE_CHECKING:
    from xhx_agent.client import LLMClient
    from xhx_agent.conversation import ConversationManager

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SessionMeta
# ---------------------------------------------------------------------------


@dataclass
class SessionMeta:
    """会话元数据：统一 SessionManager / session_dialog / session handler 的字段命名。"""

    session_id: str
    title: str = ""
    summary: str = ""
    last_active: datetime = field(default_factory=lambda: datetime.now(UTC))
    total_tokens: int = 0
    message_count: int = 0
    branch: str = ""
    file_size: int = 0

    def save(self, path: Path | str) -> None:
        """写 JSON 到 path。"""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self.session_id,
            "title": self.title,
            "summary": self.summary,
            "last_active": self.last_active.isoformat(),
            "total_tokens": self.total_tokens,
            "message_count": self.message_count,
            "branch": self.branch,
            "file_size": self.file_size,
        }
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | str) -> SessionMeta:
        """从 JSON 读回 SessionMeta。"""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        last_active = datetime.fromisoformat(data["last_active"])
        # 确保带时区（兼容旧数据无时区）
        if last_active.tzinfo is None:
            last_active = last_active.replace(tzinfo=UTC)
        return cls(
            session_id=data["session_id"],
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            last_active=last_active,
            total_tokens=data.get("total_tokens", 0),
            message_count=data.get("message_count", 0),
            branch=data.get("branch", ""),
            file_size=data.get("file_size", 0),
        )


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------


class Session:
    """会话句柄：消息逐条 append 到 `.xhx/sessions/<id>.jsonl`，close() 刷新 meta。"""

    def __init__(
        self,
        session_id: str = "",
        sessions_dir: Path | str | None = None,
        **kwargs: object,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._sessions_dir = Path(str(sessions_dir)) if sessions_dir else Path(".xhx") / "sessions"
        self.meta = SessionMeta(session_id=self.session_id)
        self._jsonl_path: Path | None = None
        self._message_count = 0
        self._title: str | None = None
        self._opened = False

    @property
    def _path(self) -> Path:
        if self._jsonl_path is None:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            self._jsonl_path = self._sessions_dir / f"{self.session_id}.jsonl"
        return self._jsonl_path

    def _ensure_open(self) -> None:
        if not self._opened:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
            self._opened = True

    def append(self, msg: object) -> None:
        """将一条 Message 逐行写入 jsonl。"""
        self._ensure_open()
        from dataclasses import asdict

        from xhx_agent.conversation import Message

        if isinstance(msg, Message):
            record: dict[str, Any] = {"type": "message", "role": msg.role, "content": msg.content}
            if msg.tool_uses:
                record["tool_uses"] = [asdict(tu) for tu in msg.tool_uses]
            if msg.tool_results:
                record["tool_results"] = [asdict(tr) for tr in msg.tool_results]
        elif isinstance(msg, dict):
            record = msg
        else:
            record = {"type": "message", "content": str(msg)}

        # 首条 user 消息前 60 字做标题
        if self._title is None and record.get("role") == "user":
            content = record.get("content", "")
            self._title = content[:60].replace("\n", " ").strip()

        self._message_count += 1
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_record(self, record: object) -> None:
        """写入一条结构化记录（compact_boundary 等）到 jsonl。"""
        self._ensure_open()
        rec = record if isinstance(record, dict) else {"type": "record", "content": str(record)}
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def close(self) -> None:
        """刷新 meta（last_active、message_count、file_size）并写 .meta 文件。"""
        self._ensure_open()
        self.meta.last_active = datetime.now(UTC)
        self.meta.message_count = self._message_count
        if self._title:
            self.meta.title = self._title
        try:
            self.meta.file_size = self._path.stat().st_size
        except OSError:
            self.meta.file_size = 0
        meta_path = self._sessions_dir / f"{self.session_id}.meta"
        self.meta.save(meta_path)


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


def _record_to_message(rec: dict[str, Any]) -> Any:
    """把一条 jsonl 记录还原成 Message；非消息记录返回 None。"""
    from xhx_agent.conversation import Message, ToolResultBlock, ToolUseBlock

    role = rec.get("role")
    if role is None:
        return None
    tool_uses = []
    for tu in rec.get("tool_uses", []) or []:
        try:
            tool_uses.append(
                ToolUseBlock(
                    tool_use_id=tu.get("tool_use_id", ""),
                    tool_name=tu.get("tool_name", ""),
                    arguments=tu.get("arguments", {}) or {},
                )
            )
        except Exception:
            pass
    tool_results = []
    for tr in rec.get("tool_results", []) or []:
        try:
            tool_results.append(
                ToolResultBlock(
                    tool_use_id=tr.get("tool_use_id", ""),
                    content=tr.get("content", ""),
                    is_error=bool(tr.get("is_error", False)),
                )
            )
        except Exception:
            pass
    return Message(
        role=role,
        content=rec.get("content", "") or "",
        tool_uses=tool_uses,
        tool_results=tool_results,
    )


class SessionManager:
    """会话管理器：create / list / cleanup。"""

    def __init__(self, work_dir: str = "") -> None:
        self._work_dir = work_dir
        self._sessions_dir = (Path(work_dir) if work_dir else Path(".")) / ".xhx" / "sessions"

    def create(self) -> Session:
        """创建新会话，生成 id 并建目录。"""
        sid = uuid.uuid4().hex[:12]
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        return Session(session_id=sid, sessions_dir=self._sessions_dir)

    def list_sessions(self) -> list[SessionMeta]:
        """扫 `.xhx/sessions/*.meta` 按 last_active 倒序返回 SessionMeta 列表。
        无 meta 时从 jsonl 头重建。"""
        result: list[SessionMeta] = []
        try:
            for meta_path in sorted(self._sessions_dir.glob("*.meta"), reverse=True):
                try:
                    meta = SessionMeta.load(meta_path)
                    result.append(meta)
                except Exception:
                    pass
        except OSError:
            pass

        # 回退：扫 jsonl 文件（没有对应 .meta 的）
        try:
            for jsonl_path in sorted(self._sessions_dir.glob("*.jsonl"), reverse=True):
                sid = jsonl_path.stem
                if any(m.session_id == sid for m in result):
                    continue
                try:
                    title = ""
                    message_count = 0
                    with open(jsonl_path, encoding="utf-8") as f:
                        for line in f:
                            if not line.strip():
                                continue
                            data = json.loads(line)
                            message_count += 1
                            if not title and data.get("role") == "user":
                                title = data.get("content", "")[:60].replace("\n", " ").strip()
                    file_size = jsonl_path.stat().st_size
                    last_active = datetime.fromtimestamp(jsonl_path.stat().st_mtime, tz=UTC)
                    result.append(
                        SessionMeta(
                            session_id=sid,
                            title=title,
                            last_active=last_active,
                            message_count=message_count,
                            file_size=file_size,
                        )
                    )
                except Exception:
                    pass
        except OSError:
            pass

        # 按 last_active 倒序
        result.sort(key=lambda m: m.last_active, reverse=True)
        return result

    def load_messages(self, session_id: str) -> list[Any]:
        """读取某会话的 jsonl，重建为 Message 列表（供 resume 用）。

        - ``type: "message"`` → 还原一条 Message（含 tool_uses / tool_results）。
        - ``type: "compact_boundary"`` → 之前的历史已被摘要替换：重置为
          [摘要消息] + keep_tail，后续 message 记录接在其后。
        """
        from xhx_agent.conversation import Message

        path = self._sessions_dir / f"{session_id}.jsonl"
        messages: list[Any] = []
        if not path.is_file():
            return messages
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    rtype = data.get("type", "message")
                    if rtype == "compact_boundary":
                        messages = []
                        summary = data.get("summary", "")
                        if summary:
                            messages.append(Message(role="user", content=f"[Earlier turns compacted]\n{summary}"))
                        for kt in data.get("keep_tail", []):
                            m = _record_to_message(kt)
                            if m is not None:
                                messages.append(m)
                    else:
                        m = _record_to_message(data)
                        if m is not None:
                            messages.append(m)
        except OSError:
            return messages
        return messages

    def open(self, session_id: str) -> Session:
        """打开一个已存在的会话句柄（续写同一 jsonl，并恢复消息计数/标题）。"""
        session = Session(session_id=session_id, sessions_dir=self._sessions_dir)
        meta_path = self._sessions_dir / f"{session_id}.meta"
        if meta_path.is_file():
            try:
                meta = SessionMeta.load(meta_path)
                session.meta = meta
                session._message_count = meta.message_count
                session._title = meta.title or None
            except Exception:
                pass
        return session

    def cleanup(self) -> None:
        """删除空会话（无消息的 jsonl）。"""
        try:
            for jsonl_path in self._sessions_dir.glob("*.jsonl"):
                try:
                    if jsonl_path.stat().st_size == 0:
                        jsonl_path.unlink(missing_ok=True)
                        # 同时删关联 meta
                        meta_path = jsonl_path.with_suffix(".meta")
                        if meta_path.exists():
                            meta_path.unlink(missing_ok=True)
                except OSError:
                    pass
        except OSError:
            pass


# ---------------------------------------------------------------------------
# generate_session_summary
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM_PROMPT = (
    "You are a session summarizer. Write exactly 1-2 short sentences in the user's "
    "language summarizing what was accomplished in this conversation. "
    "Focus on the concrete outcome — files changed, bugs fixed, features built, "
    "decisions made. Output ONLY the summary text, nothing else."
)


async def generate_session_summary(
    client: LLMClient,
    conversation: ConversationManager,
    protocol: str,
) -> str:
    """真实调一次 LLM 生成 1~2 句会话摘要；失败回退空串。"""
    if client is None or conversation is None:
        return ""

    from xhx_agent.conversation import ConversationManager, Message

    messages = conversation.history[-20:] if hasattr(conversation, "history") else []
    if not messages:
        return ""

    user_content = ""
    for m in messages:
        role = getattr(m, "role", "")
        content = getattr(m, "content", "")
        if role == "user" and content:
            user_content = content
    if not user_content:
        return ""

    summary_prompt = f"Summarize this conversation in 1-2 sentences. The user's last request was: {user_content[:500]}"

    try:
        mini_conv = ConversationManager()
        mini_conv.history = [Message(role="user", content=summary_prompt)]

        from xhx_agent.tools.base import StreamEnd, TextDelta

        collected = ""
        async for event in client.stream(mini_conv, system=_SUMMARY_SYSTEM_PROMPT):
            if isinstance(event, TextDelta):
                collected += event.text
            elif isinstance(event, StreamEnd):
                pass

        summary = collected.strip()
        if len(summary) > 200:
            summary = summary[:197] + "..."
        return summary
    except Exception:
        log.debug("generate_session_summary failed, returning empty string", exc_info=True)
        return ""


# ---------------------------------------------------------------------------
# load_instructions / make_compact_boundary
# ---------------------------------------------------------------------------


def load_instructions(work_dir: str) -> str:
    """加载项目指令。"""
    p = Path(work_dir) / "XHX.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def make_compact_boundary(summary: str, keep_tail: list[Any] | None = None, **kwargs: object) -> dict[str, Any]:
    """创建压缩边界记录（可被 session.append_record 落盘，resume 时识别）。

    返回带 ``type: "compact_boundary"`` 的 dict，内含 summary 与保留的尾部消息列表。
    """
    serialized_keep: list[dict[str, Any]] = []
    if keep_tail:
        from dataclasses import asdict

        from xhx_agent.conversation import Message

        for item in keep_tail:
            if isinstance(item, Message):
                d: dict[str, Any] = {"role": item.role, "content": item.content}
                if item.tool_uses:
                    d["tool_uses"] = [asdict(tu) for tu in item.tool_uses]
                if item.tool_results:
                    d["tool_results"] = [asdict(tr) for tr in item.tool_results]
                serialized_keep.append(d)
            elif isinstance(item, dict):
                serialized_keep.append(item)
            else:
                serialized_keep.append({"content": str(item)})

    return {
        "type": "compact_boundary",
        "summary": summary,
        "keep_tail": serialized_keep,
    }


__all__ = [
    "MEMORY_TYPES",
    "MemoryManager",
    "MemoryRecord",
    "Session",
    "SessionManager",
    "SessionMeta",
    "delete_memory",
    "find_relevant_memories",
    "generate_session_summary",
    "list_memories",
    "load_instructions",
    "make_compact_boundary",
    "memory_dir",
    "parse_memory_candidates",
    "parse_memory_file",
    "propose_memories",
    "recall_memories",
    "render_recalled_memories",
    "render_reminder",
    "slugify",
    "write_memory",
]
