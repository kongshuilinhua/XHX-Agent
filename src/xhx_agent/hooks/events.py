"""生命周期事件枚举。
"""

from __future__ import annotations

from enum import StrEnum


class LifecycleEvent(StrEnum):
    """Hook 可订阅的生命周期事件。"""

    # 会话（Session）级别
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # 轮次（Turn）级别
    TURN_START = "turn_start"
    TURN_END = "turn_end"

    # 工具（Tool）级别
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"

    # 规划 / 补丁（更细粒度，兼容旧 before_plan / before_patch）
    PRE_PLAN = "pre_plan"
    PRE_PATCH = "pre_patch"

    # 消息（Message）级别
    PRE_SEND = "pre_send"
    POST_RECEIVE = "post_receive"

    # 系统（System）级别
    STARTUP = "startup"
    SHUTDOWN = "shutdown"
    ERROR = "error"
    COMPACT = "compact"
    PERMISSION_REQUEST = "permission_request"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTE = "command_execute"
