"""agent_runner.py 集成式单测：用脚本化 fake client 驱动 agent.run() 主循环。

permission_checker=None 时工具直接执行（跳过权限），小对话使 auto_compact 成为空操作，
因此脚本化 client 能干净地驱动整个循环——一条测试即覆盖 run() 的大部分分支。
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from xhx_agent.agents.agent_runner import (
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionResponse,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
)
from xhx_agent.conversation import ConversationManager
from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import StreamEnd, TextDelta, Tool, ToolCallComplete, ToolResult


class _EchoParams(BaseModel):
    text: str = ""


class _EchoTool(Tool):
    name = "echo"
    description = "echo back the text"
    params_model = _EchoParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: _EchoParams) -> ToolResult:  # type: ignore[override]
        return ToolResult(output=f"echoed: {params.text}")


class _ScriptedClient:
    """每次 stream() 调用产出脚本里的下一组事件。"""

    def __init__(self, turns: list[list[Any]]) -> None:
        self._turns = turns
        self.calls = 0

    async def stream(self, conversation: Any, system: Any = None, tools: Any = None) -> Any:
        idx = min(self.calls, len(self._turns) - 1)
        self.calls += 1
        for ev in self._turns[idx]:
            yield ev


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_EchoTool())
    return reg


def _drive(agent: Agent, conv: ConversationManager) -> list[Any]:
    async def _run() -> list[Any]:
        return [ev async for ev in agent.run(conv)]

    return asyncio.run(_run())


def test_tool_call_then_final_text(tmp_path) -> None:
    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="echo", arguments={"text": "hi"}),
                StreamEnd(stop_reason="tool_use", input_tokens=5, output_tokens=2),
            ],
            [
                TextDelta(text="完成了"),
                StreamEnd(stop_reason="end_turn", input_tokens=8, output_tokens=3),
            ],
        ]
    )
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("echo hi")
    events = _drive(agent, conv)

    assert any(isinstance(e, ToolUseEvent) and e.tool_name == "echo" for e in events)
    assert any(isinstance(e, ToolResultEvent) and "echoed: hi" in e.output for e in events)
    assert any(isinstance(e, StreamText) and "完成了" in e.text for e in events)
    assert any(isinstance(e, TurnComplete) for e in events)
    assert any(isinstance(e, LoopComplete) for e in events)
    # 累计 token 已记账
    assert agent.total_input_tokens == 13


def test_plain_text_single_turn(tmp_path) -> None:
    client = _ScriptedClient(
        [[TextDelta(text="你好"), StreamEnd(stop_reason="end_turn", input_tokens=3, output_tokens=1)]]
    )
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("hi")
    events = _drive(agent, conv)
    assert any(isinstance(e, LoopComplete) for e in events)
    # 最终回复进入历史
    assert any(m.role == "assistant" and "你好" in m.content for m in conv.history)


def test_unknown_tool_terminates(tmp_path) -> None:
    # 连续 3 次未知工具 → ErrorEvent 终止
    unknown_turn = [
        ToolCallComplete(tool_id="x", tool_name="does_not_exist", arguments={}),
        StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
    ]
    client = _ScriptedClient([unknown_turn, unknown_turn, unknown_turn, unknown_turn])
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("call missing tool")
    events = _drive(agent, conv)
    assert any(isinstance(e, ErrorEvent) and "unknown tool" in e.message for e in events)
    # 未知工具的结果是错误
    assert any(isinstance(e, ToolResultEvent) and e.is_error for e in events)


def test_max_iterations_guard(tmp_path) -> None:
    tool_turn = [
        ToolCallComplete(tool_id="1", tool_name="echo", arguments={"text": "x"}),
        StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
    ]
    client = _ScriptedClient([tool_turn, tool_turn, tool_turn])
    agent = Agent(
        client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path), max_iterations=1
    )
    conv = ConversationManager()
    conv.add_user_message("loop forever")
    events = _drive(agent, conv)
    assert any(isinstance(e, ErrorEvent) and "maximum iterations" in e.message for e in events)


def test_set_permission_mode_updates_checker(tmp_path) -> None:
    from xhx_agent.permissions import PermissionMode

    agent = Agent(client=_ScriptedClient([[]]), registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    assert agent.plan_mode is False
    agent.set_permission_mode(PermissionMode.PLAN)
    assert agent.plan_mode is True


class _CmdParams(BaseModel):
    note: str = ""


class _CmdTool(Tool):
    name = "noop_cmd"
    description = "a command-category no-op"
    params_model = _CmdParams
    category = "command"

    async def execute(self, params: _CmdParams) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="cmd ran")


def _checker(tmp_path, mode):
    from xhx_agent.safety.permissions.checker import PermissionChecker
    from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector
    from xhx_agent.safety.permissions.rules import RuleEngine
    from xhx_agent.safety.permissions.sandbox import PathSandbox

    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(tmp_path)),
        rule_engine=RuleEngine(),
        mode=mode,
    )


def _registry_cmd() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_CmdTool())
    return reg


def _drive_answer(agent: Agent, conv: ConversationManager, answer) -> list:
    from xhx_agent.agents.agent_runner import PermissionRequest

    async def _run() -> list:
        out = []
        async for ev in agent.run(conv):
            out.append(ev)
            if isinstance(ev, PermissionRequest):
                ev.future.set_result(answer)
        return out

    return asyncio.run(_run())


def test_permission_ask_then_allow(tmp_path) -> None:
    from xhx_agent.permissions import PermissionMode

    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="noop_cmd", arguments={"note": "x"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="ok"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(
        client=client,
        registry=_registry_cmd(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path, PermissionMode.DEFAULT),
    )
    conv = ConversationManager()
    conv.add_user_message("run cmd")
    events = _drive_answer(agent, conv, PermissionResponse.ALLOW)
    assert any(isinstance(e, ToolResultEvent) and "cmd ran" in e.output for e in events)


def test_permission_ask_then_deny(tmp_path) -> None:
    from xhx_agent.permissions import PermissionMode

    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="noop_cmd", arguments={"note": "x"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="ok"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(
        client=client,
        registry=_registry_cmd(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path, PermissionMode.DEFAULT),
    )
    conv = ConversationManager()
    conv.add_user_message("run cmd")
    events = _drive_answer(agent, conv, PermissionResponse.DENY)
    assert any(isinstance(e, ToolResultEvent) and e.is_error for e in events)


def test_with_hook_engine(tmp_path) -> None:
    """带 HookEngine 跑一轮工具调用，覆盖 agent_runner 的各生命周期 hook 分支。"""
    from xhx_agent.hooks.engine import HookEngine
    from xhx_agent.hooks.loader import load_hooks

    hooks = load_hooks(
        [
            {"event": "session_start", "action": {"type": "prompt", "message": "开始"}},
            {"event": "turn_start", "action": {"type": "prompt", "message": "轮开始"}},
            {"event": "pre_send", "action": {"type": "prompt", "message": "发送前"}},
            {"event": "post_receive", "action": {"type": "command", "command": "echo recv"}},
            {"event": "pre_tool_use", "action": {"type": "command", "command": "echo pre"}},
            {"event": "post_tool_use", "action": {"type": "command", "command": "echo post"}},
            {"event": "turn_end", "action": {"type": "prompt", "message": "轮结束"}},
            {"event": "session_end", "action": {"type": "command", "command": "echo end"}},
        ]
    )
    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="echo", arguments={"text": "a"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="done"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(
        client=client,
        registry=_registry(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
        hook_engine=HookEngine(hooks),
    )
    conv = ConversationManager()
    conv.add_user_message("go")
    events = _drive(agent, conv)
    assert any(isinstance(e, LoopComplete) for e in events)
    assert any(isinstance(e, ToolResultEvent) and "echoed: a" in e.output for e in events)


def test_parallel_concurrent_tools(tmp_path) -> None:
    """一轮里两个并发安全工具 → 走并行批量执行路径。"""
    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="echo", arguments={"text": "a"}),
                ToolCallComplete(tool_id="2", tool_name="echo", arguments={"text": "b"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="done"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("two calls")
    events = _drive(agent, conv)
    outs = [e.output for e in events if isinstance(e, ToolResultEvent)]
    assert any("echoed: a" in o for o in outs) and any("echoed: b" in o for o in outs)


def test_notification_fn_injects_reminder(tmp_path) -> None:
    client = _ScriptedClient(
        [[TextDelta(text="ok"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)]]
    )
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    agent.notification_fn = lambda: ["有新消息"]
    conv = ConversationManager()
    conv.add_user_message("hi")
    _drive(agent, conv)
    # 通知被作为 system reminder 注入历史
    assert any("有新消息" in (m.content or "") for m in conv.history)


def test_bypass_mode_auto_allows(tmp_path) -> None:
    from xhx_agent.permissions import PermissionMode

    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="noop_cmd", arguments={"note": "x"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="done"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(
        client=client,
        registry=_registry_cmd(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
        permission_checker=_checker(tmp_path, PermissionMode.BYPASS),
    )
    conv = ConversationManager()
    conv.add_user_message("run cmd")
    # BYPASS：不应产生 PermissionRequest
    from xhx_agent.agents.agent_runner import PermissionRequest

    events = _drive_answer(agent, conv, PermissionResponse.DENY)
    assert not any(isinstance(e, PermissionRequest) for e in events)
    assert any(isinstance(e, ToolResultEvent) and "cmd ran" in e.output for e in events)
