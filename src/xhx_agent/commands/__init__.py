"""命令系统。"""
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from xhx_agent.commands.parser import parse_command
from xhx_agent.commands.registry import CommandInfo, CommandRegistry


class UIController(Protocol):
    """TUI 控制接口。"""

    def request_permission(self, tool_name: str, description: str) -> Any: ...
    def show_plan(self, plan: str) -> Any: ...


@dataclass
class CommandContext:
    """命令执行上下文。"""
    args: str = ""
    agent: Any = None
    conversation: Any = None
    session: Any = None
    session_manager: Any = None
    memory_manager: Any = None
    ui: UIController | None = None
    config: Any = None
    work_dir: str = ""


@dataclass
class Command:
    """命令定义。"""
    name: str
    description: str
    type: str = "LOCAL"
    handler: Callable | None = None
    aliases: list[str] = field(default_factory=list)
    usage: str = ""
    arg_prompt: str = ""


__all__ = [
    "Command",
    "CommandContext",
    "CommandInfo",
    "CommandRegistry",
    "UIController",
    "complete",
    "parse_command",
]


def complete(prefix: str, registry: Any) -> list[str]:
    """命令补全。"""
    return []

