"""Hook 数据模型：Hook 定义、上下文、动作、结果。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xhx_agent.hooks.conditions import ConditionGroup


@dataclass
class Action:
    """Hook 触发的动作定义。"""
    type: str                                    # command / prompt / http / agent
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    timeout: int = 30


@dataclass
class ActionResult:
    """动作执行结果。"""
    output: str = ""
    success: bool = True


@dataclass
class Hook:
    """单条 Hook 定义。"""
    id: str
    event: str
    action: Action
    condition: ConditionGroup | None = None
    reject: bool = False       # 拒绝工具执行（仅 pre_tool_use）
    once: bool = False         # 一次性 hook
    async_exec: bool = False   # 异步执行（不能与 pre_tool_use 同时使用）
    executed: bool = False

    def should_run(self) -> bool:
        if self.once and self.executed:
            return False
        return True

    def mark_executed(self) -> None:
        self.executed = True


@dataclass
class HookContext:
    """Hook 执行时的上下文信息，支持模板展开。"""
    event_name: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""

    def get_field(self, name: str) -> str:
        """获取上下文字段值（供条件表达式使用）。"""
        if name == "tool":
            return self.tool_name
        if name == "event":
            return self.event_name
        if name.startswith("args."):
            key = name[5:]
            value = self.tool_args.get(key, "")
            return str(value) if value else ""
        return ""

    def expand(self, template: str) -> str:
        """展开模板变量。"""
        result = template
        result = result.replace("$EVENT", self.event_name)
        result = result.replace("$TOOL_NAME", self.tool_name)
        result = result.replace("$FILE_PATH", self.file_path)
        result = result.replace("$MESSAGE", self.message)
        result = result.replace("$ERROR", self.error)
        for key, value in self.tool_args.items():
            result = result.replace(f"$TOOL_ARGS.{key}", str(value))
        return result


class ToolRejectedError(Exception):
    """Hook 拒绝工具执行时抛出的异常。"""

    def __init__(self, tool: str, reason: str, hook_id: str) -> None:
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id
        super().__init__(f"Tool '{tool}' rejected by hook '{hook_id}': {reason}")
