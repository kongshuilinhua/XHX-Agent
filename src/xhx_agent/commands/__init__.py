"""斜杠命令系统：统一注册 + 解析 + 执行。

替代 TUI 中分散的 if/elif 链。
"""

from xhx_agent.commands.parser import parse_command
from xhx_agent.commands.registry import CommandRegistry

__all__ = ["CommandRegistry", "parse_command"]
