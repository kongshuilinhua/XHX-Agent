"""memory/__init__.py 单测：SessionMeta、Session、SessionManager、摘要、compact_boundary。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from xhx_agent.conversation import ConversationManager, Message
from xhx_agent.memory import (
    Session,
    SessionManager,
    SessionMeta,
    generate_session_summary,
    make_compact_boundary,
)
from xhx_agent.tools.base import StreamEnd, TextDelta


def test_session_meta_roundtrip(tmp_path: Path) -> None:
    meta = SessionMeta(
        session_id="abc",
        title="t",
        summary="s",
        last_active=datetime(2026, 6, 1, tzinfo=UTC),
        message_count=5,
        branch="main",
        file_size=99,
    )
    p = tmp_path / "abc.meta"
    meta.save(p)
    loaded = SessionMeta.load(p)
    assert loaded.session_id == "abc" and loaded.message_count == 5 and loaded.branch == "main"


def test_session_lifecycle(tmp_path: Path) -> None:
    sm = SessionManager(str(tmp_path))
    sess = sm.create()
    sess.append(Message(role="user", content="第一条问题"))
    sess.append(Message(role="assistant", content="回答"))
    sess.close()
    # meta 落盘：标题取首条 user，消息数=2
    metas = sm.list_sessions()
    assert any(m.session_id == sess.session_id and m.message_count == 2 for m in metas)
    assert any("第一条问题" in (m.title or "") for m in metas)


def test_session_manager_jsonl_fallback(tmp_path: Path) -> None:
    # 没有 .meta，仅有 .jsonl → list_sessions 从 jsonl 头重建
    sessions_dir = tmp_path / ".xhx" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "xyz.jsonl").write_text(
        '{"type":"message","role":"user","content":"hello world"}\n', encoding="utf-8"
    )
    metas = SessionManager(str(tmp_path)).list_sessions()
    assert any(m.session_id == "xyz" and "hello world" in m.title for m in metas)


def test_session_manager_cleanup_empty(tmp_path: Path) -> None:
    sessions_dir = tmp_path / ".xhx" / "sessions"
    sessions_dir.mkdir(parents=True)
    empty = sessions_dir / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    SessionManager(str(tmp_path)).cleanup()
    assert not empty.exists()


def test_make_compact_boundary() -> None:
    rec = make_compact_boundary("摘要正文", keep_tail=[Message(role="user", content="尾部")])
    assert rec["type"] == "compact_boundary"
    assert rec["summary"] == "摘要正文"
    assert rec["keep_tail"][0]["content"] == "尾部"


def test_generate_session_summary() -> None:
    class _Client:
        async def stream(self, conversation, system=None):
            yield TextDelta(text="这是一句会话摘要")
            yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)

    conv = ConversationManager()
    conv.add_user_message("帮我写贪吃蛇")
    conv.add_assistant_message("好的")
    summary = asyncio.run(generate_session_summary(_Client(), conv, "openai-compat"))
    assert "会话摘要" in summary


def test_generate_summary_empty_conversation() -> None:
    class _Client:
        async def stream(self, conversation, system=None):
            yield StreamEnd(stop_reason="end_turn", input_tokens=0, output_tokens=0)

    assert asyncio.run(generate_session_summary(_Client(), ConversationManager(), "openai-compat")) == ""
    assert asyncio.run(generate_session_summary(None, ConversationManager(), "x")) == ""


def test_open_nonexistent_returns_fresh(tmp_path: Path) -> None:
    sm = SessionManager(str(tmp_path))
    sess = sm.open("does-not-exist")
    assert isinstance(sess, Session)
    assert sm.load_messages("does-not-exist") == []
