"""hooks/loader.py + hooks/engine.py 单测。"""

from __future__ import annotations

import asyncio

import pytest

from xhx_agent.hooks.conditions import parse_condition
from xhx_agent.hooks.engine import HookEngine
from xhx_agent.hooks.loader import HookConfigError, load_hooks
from xhx_agent.hooks.models import Action, Hook, HookContext


def test_load_hooks_valid() -> None:
    hooks = load_hooks(
        [
            {"event": "pre_send", "action": {"type": "prompt", "message": "记得跑测试"}},
            {"id": "h2", "event": "pre_tool_use", "if": 'tool == "Bash"', "action": {"type": "command", "command": "echo hi"}},
        ]
    )
    assert len(hooks) == 2
    assert hooks[0].id == "pre_send_0"  # 自动生成 id
    assert hooks[1].id == "h2" and hooks[1].condition is not None


def test_load_hooks_empty() -> None:
    assert load_hooks(None) == []
    assert load_hooks([]) == []


def test_load_hooks_errors() -> None:
    with pytest.raises(HookConfigError):
        load_hooks([{"action": {"type": "prompt", "message": "x"}}])  # 缺 event
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "bogus", "action": {"type": "prompt", "message": "x"}}])  # 非法 event
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_send"}])  # 缺 action
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_send", "action": {"type": "weird"}}])  # 非法 action type
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_send", "action": {"type": "command"}}])  # 缺 command
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_send", "reject": True, "action": {"type": "prompt", "message": "x"}}])  # reject 非 pre_tool_use
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_tool_use", "async": True, "action": {"type": "command", "command": "x"}}])  # async+pre_tool_use
    with pytest.raises(HookConfigError):
        load_hooks([{"event": "pre_send", "action": {"type": "prompt", "message": "x", "timeout": -1}}])  # 非法 timeout


def test_engine_find_matching_and_condition() -> None:
    h1 = Hook(id="a", event="pre_tool_use", action=Action(type="prompt", message="m"))
    h2 = Hook(
        id="b",
        event="pre_tool_use",
        action=Action(type="prompt", message="m2"),
        condition=parse_condition('tool == "Bash"'),
    )
    eng = HookEngine([h1, h2])
    ctx_bash = HookContext(event_name="pre_tool_use", tool_name="Bash")
    assert len(eng.find_matching_hooks("pre_tool_use", ctx_bash)) == 2
    ctx_read = HookContext(event_name="pre_tool_use", tool_name="Read")
    # h2 条件不满足
    assert len(eng.find_matching_hooks("pre_tool_use", ctx_read)) == 1
    # 事件不匹配
    assert eng.find_matching_hooks("turn_end", ctx_bash) == []


def test_engine_run_prompt_and_command() -> None:
    eng = HookEngine(
        [
            Hook(id="p", event="pre_send", action=Action(type="prompt", message="提示语")),
            Hook(id="c", event="pre_send", action=Action(type="command", command="echo done")),
        ]
    )
    asyncio.run(eng.run_hooks("pre_send", HookContext(event_name="pre_send")))
    # prompt 输出进入 prompt_messages
    msgs = eng.collect_prompt_messages()
    assert "提示语" in msgs
    assert eng.collect_prompt_messages() == []  # 已清空
    notifs = eng.drain_notifications()
    assert any(n.hook_id == "p" for n in notifs)
    assert eng.drain_notifications() == []


def test_engine_once_and_clear() -> None:
    h = Hook(id="o", event="pre_send", action=Action(type="prompt", message="x"), once=True)
    eng = HookEngine([h])
    asyncio.run(eng.run_hooks("pre_send", HookContext(event_name="pre_send")))
    # once：第二次不再匹配
    assert eng.find_matching_hooks("pre_send", HookContext(event_name="pre_send")) == []
    eng.clear()
    assert eng.hooks == []


def test_engine_run_sync() -> None:
    eng = HookEngine([Hook(id="s", event="pre_send", action=Action(type="prompt", message="sync"))])
    eng.run_hooks_sync("pre_send", HookContext(event_name="pre_send"))
    assert "sync" in eng.collect_prompt_messages()
