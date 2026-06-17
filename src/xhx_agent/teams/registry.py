"""Agent 名称注册表（单例）。来源：mewcode teams/registry.py。"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    """全局单例：agent name ↔ agent_id 双向映射。"""

    _instance: AgentNameRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._names: dict[str, str] = {}

    @staticmethod
    def instance() -> AgentNameRegistry:
        if AgentNameRegistry._instance is None:
            with AgentNameRegistry._lock:
                if AgentNameRegistry._instance is None:
                    AgentNameRegistry._instance = AgentNameRegistry()
        return AgentNameRegistry._instance

    @staticmethod
    def reset() -> None:
        with AgentNameRegistry._lock:
            AgentNameRegistry._instance = None

    def register(self, name: str, agent_id: str) -> None:
        self._names[name] = agent_id

    def resolve(self, name_or_id: str) -> str | None:
        if name_or_id in self._names:
            return self._names[name_or_id]
        for n, aid in self._names.items():
            if aid == name_or_id:
                return aid
        return None

    def unregister(self, name: str) -> None:
        self._names.pop(name, None)

    def list_all(self) -> dict[str, str]:
        return dict(self._names)
