"""Agent Teams 数据模型。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from xhx_agent.teams.progress import TeammateProgress


class BackendType(StrEnum):
    IN_PROCESS = "in-process"


@dataclass
class TeammateInfo:
    """队友信息。"""
    name: str
    agent_id: str
    agent_type: str
    model: str
    worktree_path: str
    backend_type: str = "in-process"
    is_active: bool | None = None
    progress: TeammateProgress | None = None  # 不持久化

    def to_dict(self) -> dict[str, Any]:
        d = {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "backend_type": self.backend_type,
            "is_active": self.is_active,
        }
        # progress 不序列化
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> TeammateInfo:
        return TeammateInfo(
            name=data["name"],
            agent_id=data["agent_id"],
            agent_type=data.get("agent_type", ""),
            model=data.get("model", ""),
            worktree_path=data.get("worktree_path", ""),
            backend_type=data.get("backend_type", "in-process"),
            is_active=data.get("is_active"),
        )


@dataclass
class AgentTeam:
    """Agent 团队。"""
    name: str
    lead_agent_id: str
    members: list[TeammateInfo] = field(default_factory=list)
    config_path: str = ""
    description: str = ""

    # ------------------------------------------------------------------
    # member management
    # ------------------------------------------------------------------

    def get_member(self, name: str) -> TeammateInfo | None:
        for m in self.members:
            if m.name == name or m.agent_id == name:
                return m
        return None

    def add_member(self, member: TeammateInfo) -> None:
        self.members.append(member)

    def remove_member(self, name: str) -> bool:
        for i, m in enumerate(self.members):
            if m.name == name or m.agent_id == name:
                self.members.pop(i)
                return True
        return False

    def set_member_active(self, name: str, is_active: bool) -> bool:
        m = self.get_member(name)
        if m is None:
            return False
        m.is_active = is_active
        return True

    def all_idle(self) -> bool:
        return all(not (m.is_active) for m in self.members)

    def active_members(self) -> list[TeammateInfo]:
        return [m for m in self.members if m.is_active is not False]

    # ------------------------------------------------------------------
    # persistence
    # ------------------------------------------------------------------

    __TEAM_DIR = ".xhx/teams"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "lead_agent_id": self.lead_agent_id,
            "members": [m.to_dict() for m in self.members],
            "description": self.description,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> AgentTeam:
        return AgentTeam(
            name=data["name"],
            lead_agent_id=data["lead_agent_id"],
            members=[TeammateInfo.from_dict(m) for m in data.get("members", [])],
            description=data.get("description", ""),
        )

    def save(self) -> None:
        path = Path(self.config_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def load(config_path: str) -> AgentTeam:
        data = json.loads(Path(config_path).read_text(encoding="utf-8"))
        team = AgentTeam.from_dict(data)
        team.config_path = config_path
        return team


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sanitize_name(name: str) -> str:
    import re
    name = name.lower()
    name = re.sub(r"[^a-z0-9_-]", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")
    return name


def resolve_team_dir(team_name: str) -> Path:
    return Path.home() / _AGENT_TEAM_DIR / _sanitize_name(team_name)


def unique_team_name(base: str) -> str:
    slug = _sanitize_name(base)
    path = resolve_team_dir(slug)
    if not path.exists():
        return slug
    i = 2
    while True:
        candidate = f"{slug}-{i}"
        if not resolve_team_dir(candidate).exists():
            return candidate
        i += 1


_AGENT_TEAM_DIR = ".xhx/teams"
