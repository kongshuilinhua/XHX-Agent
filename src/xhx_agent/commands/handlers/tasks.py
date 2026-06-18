"""任务管理命令。"""

from typing import Any


def create_tasks_command(manager: Any, **kwargs: Any) -> Any:
    from xhx_agent.commands import Command

    return Command(name="tasks", description="Manage background tasks")
