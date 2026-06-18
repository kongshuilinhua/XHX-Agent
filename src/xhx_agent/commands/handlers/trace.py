"""Trace 命令。"""

from typing import Any


def create_trace_command(manager: Any, agent_id: str = "", **kwargs: Any) -> Any:
    from xhx_agent.commands import Command

    return Command(name="trace", description="View agent trace")
