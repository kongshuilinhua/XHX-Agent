"""命令处理器包。"""

from typing import Any


def register_all_commands(registry: Any, **kwargs: Any) -> None:
    """注册所有内置命令。"""
    # 委托给 defaults 模块
    from xhx_agent.commands.defaults import register_default_commands

    register_default_commands(registry)
