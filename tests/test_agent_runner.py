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


class _DeferredTool(Tool):
    name = "rare_tool"
    description = "rarely used deferred tool"
    params_model = _EchoParams
    category = "read"
    should_defer = True

    async def execute(self, params: _EchoParams) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="ok")


def test_deferred_reminder_injected_once(tmp_path) -> None:
    """deferred 工具提醒内容逐轮相同，只注入一次；重复注入只会膨胀历史。"""
    reg = _registry()
    reg.register(_DeferredTool())
    client = _ScriptedClient(
        [
            [
                ToolCallComplete(tool_id="1", tool_name="echo", arguments={"text": "a"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [
                ToolCallComplete(tool_id="2", tool_name="echo", arguments={"text": "b"}),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="done"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )
    agent = Agent(client=client, registry=reg, protocol="openai-compat", work_dir=str(tmp_path))
    conv = ConversationManager()
    conv.add_user_message("go")
    _drive(agent, conv)
    reminders = [m for m in conv.history if "deferred tools" in (m.content or "")]
    assert len(reminders) == 1


def test_hook_prompt_goes_to_reminder_not_system(tmp_path) -> None:
    """prompt 型 hook 输出以 system reminder 追加进对话（append-only），
    system prompt 全程逐字稳定（不随 hook 触发抖动，保住前缀缓存）。"""

    class _FakeHookEngine:
        def __init__(self) -> None:
            self._prompts = ["项目规范：先跑测试"]

        async def run_hooks(self, event: str, ctx: Any) -> None:
            return None

        def drain_notifications(self) -> list[Any]:
            return []

        def collect_prompt_messages(self) -> list[str]:
            msgs, self._prompts = self._prompts, []
            return msgs

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
        hook_engine=_FakeHookEngine(),  # type: ignore[arg-type]
    )
    conv = ConversationManager()
    conv.add_user_message("go")
    _drive(agent, conv)
    hook_msgs = [m for m in conv.history if "Hook context:" in (m.content or "")]
    assert len(hook_msgs) == 1 and "项目规范：先跑测试" in hook_msgs[0].content


def test_model_turn_trace_records_cache_fields(tmp_path) -> None:
    """model_turn trace 落真实缓存用量（cache_read/cache_creation），供 replay 汇总命中率。"""

    class _FakeStore:
        def __init__(self) -> None:
            self.entries: list[tuple[str, dict]] = []

        def write_trace(self, entry_type: str, payload: dict) -> None:
            self.entries.append((entry_type, payload))

    client = _ScriptedClient(
        [
            [
                TextDelta(text="hi"),
                StreamEnd(stop_reason="end_turn", input_tokens=3, output_tokens=1, cache_read=7, cache_creation=2),
            ]
        ]
    )
    agent = Agent(client=client, registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    store = _FakeStore()
    agent.trace_store = store
    conv = ConversationManager()
    conv.add_user_message("hi")
    _drive(agent, conv)
    model_turns = [p for t, p in store.entries if t == "model_turn"]
    assert model_turns and model_turns[0]["cache_read"] == 7 and model_turns[0]["cache_creation"] == 2


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


# --- 回归：交互式 run() 的 changed_files 跟踪 + 完成前验证关卡 opt-in ---


class _WriteParams(BaseModel):
    file_path: str = ""
    content: str = ""


class _FakeWriteTool(Tool):
    """名为 WriteFile 的假写工具（命中 _MUTATION_TOOLS）；非并发安全→走 run() 串行分支。"""

    name = "WriteFile"
    description = "fake write"
    params_model = _WriteParams
    category = "write"

    async def execute(self, params: _WriteParams) -> ToolResult:  # type: ignore[override]
        return ToolResult(output=f"wrote {params.file_path}")


def _write_then_done_client() -> _ScriptedClient:
    return _ScriptedClient(
        [
            [
                ToolCallComplete(
                    tool_id="1", tool_name="WriteFile", arguments={"file_path": "src/foo.py", "content": "x"}
                ),
                StreamEnd(stop_reason="tool_use", input_tokens=1, output_tokens=1),
            ],
            [TextDelta(text="done"), StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)],
        ]
    )


def _write_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_FakeWriteTool())
    return reg


def test_interactive_run_tracks_changed_files(tmp_path) -> None:
    """交互式 run() 在写类工具成功后必须记录 changed_files（此前只 headless 记录，TUI 哑火）。"""
    agent = Agent(
        client=_write_then_done_client(), registry=_write_registry(), protocol="openai-compat", work_dir=str(tmp_path)
    )
    conv = ConversationManager()
    conv.add_user_message("write foo")
    _drive(agent, conv)
    assert "src/foo.py" in agent.changed_files


def test_verification_gate_off_by_default(tmp_path, monkeypatch) -> None:
    """默认 verification_gate=False：即使有改动也不触碰验证关卡。"""
    import xhx_agent.verification.router as router

    hits: list[int] = []
    monkeypatch.setattr(router, "infer_verification", lambda ws, cf: hits.append(1))

    agent = Agent(
        client=_write_then_done_client(), registry=_write_registry(), protocol="openai-compat", work_dir=str(tmp_path)
    )
    conv = ConversationManager()
    conv.add_user_message("write foo")
    events = _drive(agent, conv)
    assert hits == []  # 关卡未触发
    assert any(isinstance(e, LoopComplete) for e in events)


def test_verification_gate_runs_when_enabled(tmp_path, monkeypatch) -> None:
    """verification_gate=True 且有改动时，收尾轮执行验证命令（这里用无害 echo 验证关卡确实跑）。"""
    import xhx_agent.verification.router as router
    from xhx_agent.verification.router import VerificationCommand, VerificationPlan

    monkeypatch.setattr(
        router,
        "infer_verification",
        lambda ws, cf: VerificationPlan(commands=[VerificationCommand(command="echo ok", reason="test")]),
    )

    agent = Agent(
        client=_write_then_done_client(), registry=_write_registry(), protocol="openai-compat", work_dir=str(tmp_path)
    )
    agent.verification_gate = True
    conv = ConversationManager()
    conv.add_user_message("write foo")
    _drive(agent, conv)
    assert getattr(agent, "_verification_passed", False) is True


# --- auto 模式 LLM 分类器 ---


class _VerdictClient:
    """固定吐一句裁决文本的 fake client，用于测 auto 分类器解析。"""

    def __init__(self, verdict: str) -> None:
        self.verdict = verdict

    async def stream(self, conversation: Any, system: Any = None, tools: Any = None) -> Any:
        yield TextDelta(text=self.verdict)
        yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)


def test_classify_command_parses_verdict(tmp_path) -> None:
    a = Agent(
        client=_VerdictClient("ALLOW looks safe"),
        registry=_registry(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
    )
    b = Agent(
        client=_VerdictClient("BLOCK destructive"),
        registry=_registry(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
    )
    assert asyncio.run(a._classify_command("mkdir x")) is True
    assert asyncio.run(b._classify_command("rm -rf x")) is False


def test_classify_command_safe_failsafe(tmp_path) -> None:
    class _BoomClient:
        async def stream(self, conversation: Any, system: Any = None, tools: Any = None) -> Any:
            raise RuntimeError("boom")
            yield  # pragma: no cover  使其成为 async generator

    a = Agent(client=_BoomClient(), registry=_registry(), protocol="openai-compat", work_dir=str(tmp_path))
    # 分类器异常时保守返回 False（转人工确认）
    assert asyncio.run(a._classify_command_safe("anything")) is False


def test_classifier_client_takes_precedence(tmp_path) -> None:
    # 配了便宜分类器 client 时，_classify_command 用它（ALLOW）而非主 client（BLOCK）
    a = Agent(
        client=_VerdictClient("BLOCK from main"),
        registry=_registry(),
        protocol="openai-compat",
        work_dir=str(tmp_path),
    )
    a.classifier_client = _VerdictClient("ALLOW from cheap")
    assert asyncio.run(a._classify_command("mkdir x")) is True
