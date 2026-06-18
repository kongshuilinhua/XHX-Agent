"""命令注册表：声明式注册 + 查找 + Tab 补全。

替代 TUI 中的 if/elif 链和重复的命令列表。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandInfo:
    """单个命令的元数据。"""
    name: str
    description: str
    handler: Callable[..., str | bool | None] = field(repr=False)
    needs_arg: bool = False
    arg_hint: str = ""  # 如 "profile_name" / "run_id"


# 命令处理函数签名: (app, argument: str) -> str | bool | None
CommandHandler = Callable[[Any, str], str | bool | None]


class CommandRegistry:
    """斜杠命令注册表。"""

    def __init__(self) -> None:
        self._commands: dict[str, CommandInfo] = {}

    # ------------------------------------------------------------------
    # registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        description: str,
        handler: CommandHandler,
        *,
        needs_arg: bool = False,
        arg_hint: str = "",
    ) -> None:
        """注册一条命令。"""
        self._commands[name] = CommandInfo(
            name=name,
            description=description,
            handler=handler,
            needs_arg=needs_arg,
            arg_hint=arg_hint,
        )

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------

    def execute(self, app: Any, command_line: str) -> str | bool | None:
        """解析并执行一条命令。返回 handler 的返回值。"""
        from xhx_agent.commands.parser import parse_command

        command, argument, _ = parse_command(command_line)
        if not command:
            return None

        info = self._commands.get(command)
        if info is None:
            return f"Unknown command: {command}. Type /help for available commands."

        try:
            return info.handler(app, argument)
        except Exception as e:
            return f"Command error ({command}): {e}"

    def has(self, command: str) -> bool:
        return command in self._commands

    # ------------------------------------------------------------------
    # completion / listing
    # ------------------------------------------------------------------

    def list_all(self) -> list[tuple[str, str, str]]:
        """返回 [(name, description, arg_hint), ...] 供补全和帮助。"""
        return [
            (info.name, info.description, info.arg_hint)
            for info in self._commands.values()
        ]

    def matching(self, prefix: str) -> list[tuple[str, str, str]]:
        """返回匹配 *prefix* 的命令列表（Tab 补全用）。"""
        return [
            (info.name, info.description, info.arg_hint)
            for info in self._commands.values()
            if info.name.startswith(prefix)
        ]

    def register_sync(self, command: Any) -> None:
        """同步注册一个 Command 对象（新 TUI 兼容接口）。"""
        if command is None:
            return
        self.register(
            name=command.name,
            description=command.description,
            handler=command.handler or (lambda app, arg: None),
            needs_arg=bool(command.arg_prompt),
            arg_hint=command.arg_prompt or "",
        )

    def __len__(self) -> int:
        return len(self._commands)
