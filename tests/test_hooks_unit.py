"""hooks 单测：条件解析/求值、HookContext 模板、executors。"""

from __future__ import annotations

import asyncio

import pytest

from xhx_agent.hooks.conditions import (
    Condition,
    ConditionGroup,
    ConditionParseError,
    parse_condition,
)
from xhx_agent.hooks.executors import (
    _EXECUTOR_MAP,
    execute_action,
    execute_command,
    execute_prompt,
    execute_verification,
)
from xhx_agent.hooks.models import Action, ActionResult, Hook, HookContext


def _ctx(**kwargs) -> HookContext:
    return HookContext(**kwargs)


def test_get_field_and_expand() -> None:
    ctx = _ctx(event_name="pre_tool_use", tool_name="Bash", tool_args={"command": "ls"}, file_path="a.py")
    assert ctx.get_field("tool") == "Bash"
    assert ctx.get_field("event") == "pre_tool_use"
    assert ctx.get_field("args.command") == "ls"
    assert ctx.get_field("args.missing") == ""
    assert ctx.get_field("unknown") == ""
    expanded = ctx.expand("$EVENT/$TOOL_NAME/$FILE_PATH/$TOOL_ARGS.command")
    assert expanded == "pre_tool_use/Bash/a.py/ls"


def test_condition_operators() -> None:
    ctx = _ctx(tool_name="Bash", tool_args={"command": "rm -rf /tmp/x"})
    assert Condition("tool", "==", "Bash").evaluate(ctx) is True
    assert Condition("tool", "!=", "Read").evaluate(ctx) is True
    assert Condition("args.command", "=~", "rm .*").evaluate(ctx) is True
    assert Condition("args.command", "=~", "/^rm/").evaluate(ctx) is True
    assert Condition("args.command", "~=", "rm*").evaluate(ctx) is True
    assert Condition("tool", "??", "x").evaluate(ctx) is False  # 未知操作符
    # 非法正则不抛错，返回 False
    assert Condition("args.command", "=~", "(unclosed").evaluate(ctx) is False


def test_condition_group_logic() -> None:
    ctx = _ctx(tool_name="Bash", event_name="pre_tool_use")
    g_and = ConditionGroup([Condition("tool", "==", "Bash"), Condition("event", "==", "pre_tool_use")], "and")
    assert g_and.evaluate(ctx) is True
    g_and_fail = ConditionGroup([Condition("tool", "==", "Bash"), Condition("event", "==", "post")], "and")
    assert g_and_fail.evaluate(ctx) is False
    g_or = ConditionGroup([Condition("tool", "==", "Read"), Condition("event", "==", "pre_tool_use")], "or")
    assert g_or.evaluate(ctx) is True
    assert ConditionGroup([]).evaluate(ctx) is True  # 空组恒真


def test_parse_condition() -> None:
    assert parse_condition("") is None
    g = parse_condition('tool == "Bash"')
    assert g is not None and g.conditions[0].value == "Bash"
    g_and = parse_condition('tool == "Bash" && args.command =~ "rm"')
    assert g_and is not None and g_and.logic == "and" and len(g_and.conditions) == 2
    g_or = parse_condition('event == "a" || event == "b"')
    assert g_or is not None and g_or.logic == "or"
    with pytest.raises(ConditionParseError):
        parse_condition('a == "1" && b == "2" || c == "3"')  # 混用 && ||
    with pytest.raises(ConditionParseError):
        parse_condition("no_operator_here")


def test_hook_once_semantics() -> None:
    h = Hook(id="h1", event="pre_tool_use", action=Action(type="command", command="echo hi"), once=True)
    assert h.should_run() is True
    h.mark_executed()
    assert h.should_run() is False


def test_execute_command_success_and_fail() -> None:
    ok = asyncio.run(execute_command(Action(type="command", command="echo hello"), _ctx()))
    assert isinstance(ok, ActionResult) and ok.success is True
    assert "hello" in ok.output
    bad = asyncio.run(execute_command(Action(type="command", command="exit 3"), _ctx()))
    assert bad.success is False


def test_execute_verification_skips_without_changes(tmp_path) -> None:
    # 空目录（无项目标记）→ 推断不出命令 → 视为成功跳过
    res = asyncio.run(execute_verification(Action(type="verification"), _ctx(work_dir=str(tmp_path))))
    assert isinstance(res, ActionResult)
    assert res.success is True


def test_execute_prompt_expands() -> None:
    res = asyncio.run(execute_prompt(Action(type="prompt", message="hi $TOOL_NAME"), _ctx(tool_name="Bash")))
    assert res.success is True and res.output == "hi Bash"


def test_execute_action_dispatch_and_unknown() -> None:
    ok = asyncio.run(execute_action(Action(type="prompt", message="x"), _ctx()))
    assert ok.success is True and ok.output == "x"
    unknown = asyncio.run(execute_action(Action(type="nope"), _ctx()))
    assert unknown.success is False
    assert "Unknown action type" in unknown.output


def test_executors_registry_complete() -> None:
    for key in ("command", "prompt", "http", "verification"):
        assert key in _EXECUTOR_MAP
    # "agent" 动作已停用：执行器表里不应再有它
    assert "agent" not in _EXECUTOR_MAP
