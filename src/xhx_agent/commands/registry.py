"""命令注册表：声明式注册 + 查找 + Tab 补全。

同时兼容旧式 CommandInfo（同步 handler）和新式 Command（异步 handler + 别名）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CommandInfo:
    """单个命令的元数据（旧式兼容）。"""

    name: str
    description: str
    handler: Callable[..., str | bool | None] = field(repr=False)
    needs_arg: bool = False
    arg_hint: str = ""  # 如 "profile_name" / "run_id"


# 命令处理函数签名: (app, argument: str) -> str | bool | None
CommandHandler = Callable[[Any, str], str | bool | None]


class CommandRegistry:
    """斜杠命令注册表。

    存储层：内部同时维护 _commands（CommandInfo 旧式）和 _commands_new（Command 新式）。
    list_commands() / find() 返回新式 Command，供 TUI 的 _dispatch_command 使用。
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandInfo] = {}
        self._commands_new: dict[str, Any] = {}  # str → Command
        self._alias_map: dict[str, str] = {}  # alias → canonical name

    # ------------------------------------------------------------------
    # registration (旧式兼容)
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
        """注册一条旧式命令（同步 handler）。"""
        self._commands[name] = CommandInfo(
            name=name,
            description=description,
            handler=handler,
            needs_arg=needs_arg,
            arg_hint=arg_hint,
        )

    # ------------------------------------------------------------------
    # registration (新式)
    # ------------------------------------------------------------------

    def register_sync(self, command: Any) -> None:
        """注册新式 Command 对象。同时填充旧式 _commands 以保证兼容。"""
        if command is None:
            return
        self._commands_new[command.name] = command
        # 别名映射
        for alias in getattr(command, "aliases", []) or []:
            self._alias_map[alias] = command.name
        # 同步到旧式存储
        self._commands[command.name] = CommandInfo(
            name=command.name,
            description=command.description,
            handler=command.handler or (lambda app, arg: None),
            needs_arg=bool(getattr(command, "arg_prompt", "")),
            arg_hint=getattr(command, "arg_prompt", "") or "",
        )

    # ------------------------------------------------------------------
    # lookup (新式 —— TUI _dispatch_command 使用)
    # ------------------------------------------------------------------

    def list_commands(self) -> list[Any]:
        """返回所有已注册的新式 Command 对象（不含隐藏命令）。"""
        return list(self._commands_new.values())

    def find(self, name: str) -> Any | None:
        """按名称或别名查找 Command。"""
        if name in self._commands_new:
            return self._commands_new[name]
        canonical = self._alias_map.get(name)
        if canonical:
            return self._commands_new.get(canonical)
        return None

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
        return command in self._commands or command in self._alias_map

    # ------------------------------------------------------------------
    # completion / listing (旧式)
    # ------------------------------------------------------------------

    def list_all(self) -> list[tuple[str, str, str]]:
        """返回 [(name, description, arg_hint), ...] 供补全和帮助。"""
        return [(info.name, info.description, info.arg_hint) for info in self._commands.values()]

    def matching(self, prefix: str) -> list[tuple[str, str, str]]:
        """返回匹配 *prefix* 的命令列表（Tab 补全用）。"""
        return [
            (info.name, info.description, info.arg_hint)
            for info in self._commands.values()
            if info.name.startswith(prefix)
        ]

    def __len__(self) -> int:
        return len(self._commands)
