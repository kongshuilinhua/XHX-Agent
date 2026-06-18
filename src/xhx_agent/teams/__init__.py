"""Agent Teams 系统：协调者模式多 Agent 协作。"""

from xhx_agent.teams.coordinator import get_coordinator_system_prompt
from xhx_agent.teams.manager import TeamManager
from xhx_agent.teams.models import AgentTeam, BackendType, TeammateInfo
from xhx_agent.teams.registry import AgentNameRegistry

__all__ = [
    "AgentNameRegistry",
    "AgentTeam",
    "BackendType",
    "TeamManager",
    "TeammateInfo",
    "get_coordinator_system_prompt",
]
