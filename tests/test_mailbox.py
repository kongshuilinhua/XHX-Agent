"""teams/mailbox.py 单测：文件型 agent 邮箱读写/消费/广播/清理。"""

from __future__ import annotations

from pathlib import Path

from xhx_agent.teams.mailbox import Mailbox, MailboxMessage, create_message


def test_create_message_defaults() -> None:
    m = create_message("a", "b", "hi")
    assert m.from_agent == "a" and m.to_agent == "b" and m.content == "hi"
    assert m.id and m.timestamp > 0 and m.message_type == "text"


def test_write_read_consume(tmp_path: Path) -> None:
    box = Mailbox(tmp_path)
    box.write("worker", create_message("lead", "worker", "task 1"))
    box.write("worker", create_message("lead", "worker", "task 2"))
    # read 不删除
    assert len(box.read("worker")) == 2
    assert len(box.read("worker")) == 2
    # consume 读后删除
    consumed = box.consume("worker")
    assert len(consumed) == 2
    assert box.read("worker") == []


def test_read_empty_agent(tmp_path: Path) -> None:
    assert Mailbox(tmp_path).read("nobody") == []
    assert Mailbox(tmp_path).consume("nobody") == []


def test_broadcast_excludes(tmp_path: Path) -> None:
    box = Mailbox(tmp_path)
    box.broadcast(["a", "b", "c"], create_message("lead", "all", "notice"), exclude="b")
    assert len(box.read("a")) == 1
    assert box.read("b") == []
    assert len(box.read("c")) == 1


def test_cleanup_and_cleanup_all(tmp_path: Path) -> None:
    box = Mailbox(tmp_path)
    box.write("a", create_message("x", "a", "m"))
    box.write("b", create_message("x", "b", "m"))
    box.cleanup("a")
    assert box.read("a") == []
    box.cleanup_all()
    assert box.read("b") == []


def test_message_roundtrip_dict() -> None:
    m = create_message("a", "b", "c", summary="s", metadata={"k": 1})
    d = m.to_dict()
    restored = MailboxMessage.from_dict(d)
    assert restored.summary == "s" and restored.metadata == {"k": 1}
    # 多余字段被忽略
    assert MailboxMessage.from_dict({**d, "junk": 1}).content == "c"
