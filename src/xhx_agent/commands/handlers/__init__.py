"""命令处理器包 —— 注册所有内置斜杠命令。"""

from __future__ import annotations

from typing import Any

from xhx_agent.commands.handlers.clear import CLEAR_COMMAND
from xhx_agent.commands.handlers.compact import COMPACT_COMMAND
from xhx_agent.commands.handlers.help import HELP_COMMAND
from xhx_agent.commands.handlers.mcp import MCP_COMMAND
from xhx_agent.commands.handlers.memory import MEMORY_COMMAND
from xhx_agent.commands.handlers.permission import PERMISSION_COMMAND
from xhx_agent.commands.handlers.plan import PLAN_COMMAND
from xhx_agent.commands.handlers.review import REVIEW_COMMAND
from xhx_agent.commands.handlers.rewind import REWIND_COMMAND
from xhx_agent.commands.handlers.session import SESSION_COMMAND
from xhx_agent.commands.handlers.skill import SKILL_COMMAND
from xhx_agent.commands.handlers.status import STATUS_COMMAND


def register_all_commands(registry: Any, **kwargs: Any) -> None:
    """注册所有内置命令（新式 Command 对象）。"""
    # 新命令处理器
    registry.register_sync(CLEAR_COMMAND)
    registry.register_sync(COMPACT_COMMAND)
    registry.register_sync(HELP_COMMAND)
    registry.register_sync(MCP_COMMAND)
    registry.register_sync(MEMORY_COMMAND)
    registry.register_sync(PERMISSION_COMMAND)
    registry.register_sync(PLAN_COMMAND)
    registry.register_sync(REVIEW_COMMAND)
    registry.register_sync(REWIND_COMMAND)
    registry.register_sync(SESSION_COMMAND)
    registry.register_sync(SKILL_COMMAND)
    registry.register_sync(STATUS_COMMAND)

    # 保留 defaults.py 中的残留命令（exit / model 等）
    from xhx_agent.commands.defaults import register_default_commands

    register_default_commands(registry)
