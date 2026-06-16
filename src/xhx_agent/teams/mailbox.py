"""Agent 间消息信箱。来源：mewcode teams/mailbox.py。"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MailboxMessage:
    id: str
    from_agent: str
    to_agent: str
    content: str
    summary: str = ""
    message_type: str = "text"
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "from_agent": self.from_agent,
            "to_agent": self.to_agent, "content": self.content,
            "summary": self.summary, "message_type": self.message_type,
            "timestamp": self.timestamp, "metadata": self.metadata,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> MailboxMessage:
        return MailboxMessage(**data)


def create_message(
    from_agent: str, to_agent: str, content: str,
    summary: str = "", message_type: str = "text",
    metadata: dict[str, Any] | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        id=uuid.uuid4().hex[:12],
        from_agent=from_agent, to_agent=to_agent,
        content=content, summary=summary,
        message_type=message_type,
        metadata=metadata or {},
    )


class Mailbox:
    """文件系统消息信箱。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _agent_dir(self, agent_id: str) -> Path:
        d = self.base_dir / agent_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write(self, agent_id: str, message: MailboxMessage) -> None:
        ad = self._agent_dir(agent_id)
        filename = f"{message.timestamp:.6f}_{message.id}.json"
        (ad / filename).write_text(
            json.dumps(message.to_dict(), ensure_ascii=False), encoding="utf-8")

    def read(self, agent_id: str) -> list[MailboxMessage]:
        ad = self._agent_dir(agent_id)
        msgs: list[MailboxMessage] = []
        for f in sorted(ad.iterdir()):
            if f.suffix == ".json":
                try:
                    msgs.append(MailboxMessage.from_dict(
                        json.loads(f.read_text(encoding="utf-8"))))
                except Exception:
                    pass
        return msgs

    def consume(self, agent_id: str) -> list[MailboxMessage]:
        msgs = self.read(agent_id)
        ad = self._agent_dir(agent_id)
        for f in ad.iterdir():
            if f.suffix == ".json":
                f.unlink()
        return msgs

    def broadcast(self, team_members: list, message: MailboxMessage, exclude: str = "") -> None:
        for m in team_members:
            if hasattr(m, 'agent_id') and m.agent_id != exclude:
                self.write(m.agent_id, message)

    def cleanup(self, agent_id: str) -> None:
        ad = self.base_dir / agent_id
        if ad.exists():
            import shutil
            shutil.rmtree(ad)

    def cleanup_all(self) -> None:
        if self.base_dir.exists():
            import shutil
            shutil.rmtree(self.base_dir)
