"""命令系统。"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from xhx_agent.commands.parser import parse_command
from xhx_agent.commands.registry import CommandInfo, CommandRegistry


class UIController(Protocol):
    """TUI 控制接口。"""

    def add_system_message(self, text: str) -> Any: ...
    def get_token_count(self) -> Any: ...
    def set_plan_mode(self, enabled: bool) -> Any: ...
    def send_user_message(self, text: str) -> Any: ...
    def refresh_status(self) -> Any: ...


@dataclass
class CommandContext:
    """命令执行上下文。"""

    args: str = ""
    agent: Any = None
    conversation: Any = None
    session: Any = None
    session_manager: Any = None
    memory_manager: Any = None
    # 实际仅由 TUI 构造并恒传 ui=self；非交互场景不会派发命令。类型按非 Optional
    # 处理以避免每个 handler 都要做 None 收窄；默认值 None 保留以兼容 dataclass 字段顺序。
    ui: UIController = None  # type: ignore[assignment]
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


def complete(prefix: str, registry: Any) -> list[tuple[str, str]]:
    """命令补全：返回匹配 prefix 的 [(显示文本, 补全值), ...]。

    优先匹配新式 Command（list_commands），回退旧式 matching()。
    """
    prefix = prefix.lstrip("/").lower() if prefix else ""
    results: list[tuple[str, str]] = []

    # 新式 Command
    if hasattr(registry, "list_commands"):
        for cmd in registry.list_commands():
            name = getattr(cmd, "name", "")
            if name and name.startswith(prefix):
                display = f"/{name}"
                if getattr(cmd, "arg_prompt", ""):
                    display += f"  ({cmd.arg_prompt})"
                results.append((display, name))
            # 别名匹配
            for alias in getattr(cmd, "aliases", []) or []:
                if alias.startswith(prefix):
                    results.append((f"/{alias} → /{name}", name))

    # 旧式 matching（兼容）
    if not results and hasattr(registry, "matching"):
        for name, _desc, hint in registry.matching(prefix):
            display = f"/{name}"
            if hint:
                display += f"  ({hint})"
            results.append((display, name))

    return results
