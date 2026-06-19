"""Slash 命令 handler 烟雾测试：用真实 registry/conversation/memory 驱动每个命令，
确保无崩溃，并对历史上出过 bug 的命令做针对性断言。

注意：故意使用 **真实** ToolRegistry（list_tools 返回 Tool 对象而非名字）与真实
ConversationManager / MemoryManager，这样像"把 Tool 对象当字符串用"这类 bug 才能被抓到。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from xhx_agent.commands import CommandContext, CommandRegistry
from xhx_agent.commands.defaults import register_default_commands
from xhx_agent.commands.handlers import register_all_commands
from xhx_agent.commands.handlers.tasks import create_tasks_command
from xhx_agent.commands.handlers.trace import create_trace_command
from xhx_agent.commands.handlers.worktree import create_worktree_command
from xhx_agent.conversation import ConversationManager
from xhx_agent.memory.auto_memory import MemoryManager
from xhx_agent.permissions import PermissionMode
from xhx_agent.tools import create_default_registry


class _FakeTask:
    def __init__(self, done: bool = False) -> None:
        self._done = done
        self.cancelled = False

    def done(self) -> bool:
        return self._done

    def cancel(self) -> None:
        self.cancelled = True


class _FakeAgent:
    def __init__(self, registry: Any, work_dir: str) -> None:
        self.registry = registry
        self.permission_mode = PermissionMode.DEFAULT
        self.context_window = 128_000
        self.work_dir = work_dir
        self.session_id = ""
        self.profile = "test-profile"
        self.set_permission_mode_calls: list[PermissionMode] = []

    @property
    def plan_mode(self) -> bool:
        return self.permission_mode == PermissionMode.PLAN

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.permission_mode = mode
        self.set_permission_mode_calls.append(mode)


class _FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.verbose = False
        self.plan_mode_calls: list[bool] = []
        self.sent: list[str] = []
        self.refreshed = 0
        self.graceful_called = False
        self._agent_task: _FakeTask | None = None

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    def get_token_count(self) -> tuple[int, int]:
        return (0, 0)

    def set_plan_mode(self, enabled: bool) -> None:
        self.plan_mode_calls.append(enabled)

    def send_user_message(self, text: str) -> None:
        self.sent.append(text)

    def refresh_status(self) -> None:
        self.refreshed += 1

    async def graceful_exit(self) -> None:
        self.graceful_called = True


class _FakeSessionManager:
    def list_sessions(self) -> list[Any]:
        return []


class _FakeSkillLoader:
    def list_all(self) -> list[Any]:
        return []


class _FakeWorktreeMgr:
    def list_worktrees(self) -> list[Any]:
        return []


class _FakeTaskMgr:
    def list_tasks(self) -> list[Any]:
        return []


class _FakeTraceMgr:
    def __init__(self) -> None:
        self._nodes: dict[str, Any] = {}


def _build_ctx(tmp_path: Path) -> tuple[CommandContext, _FakeUI, _FakeAgent, CommandRegistry]:
    registry = CommandRegistry()
    register_default_commands(registry)
    register_all_commands(registry)
    registry.register_sync(create_worktree_command(_FakeWorktreeMgr()))
    registry.register_sync(create_tasks_command(_FakeTaskMgr()))
    registry.register_sync(create_trace_command(_FakeTraceMgr()))

    tool_registry = create_default_registry()
    agent = _FakeAgent(tool_registry, str(tmp_path))
    ui = _FakeUI()
    conv = ConversationManager()
    conv.add_user_message("你好")
    conv.add_assistant_message("你好，有什么可以帮你？")

    rendered: list[Any] = []

    async def _render_restored(msgs: Any) -> None:
        rendered.append(msgs)

    ctx = CommandContext(
        args="",
        agent=agent,
        conversation=conv,
        session=None,
        session_manager=_FakeSessionManager(),
        memory_manager=MemoryManager(str(tmp_path)),
        ui=ui,
        config={
            "registry": registry,
            "set_session": lambda s: None,
            "set_conversation": lambda c: None,
            "clear_chat": lambda: None,
            "render_restored": _render_restored,
            "skill_loader": _FakeSkillLoader(),
            "skill_executor": None,
        },
    )
    return ctx, ui, agent, registry


def _run(handler: Any, ctx: CommandContext) -> None:
    asyncio.run(handler(ctx))


def test_every_command_handler_runs_without_crash(tmp_path: Path) -> None:
    """注册表里每条命令的 handler 用空参调用一遍，任何异常都让测试失败。"""
    ctx, ui, _agent, registry = _build_ctx(tmp_path)
    for cmd in registry.list_commands():
        ctx.args = ""
        assert cmd.handler is not None, f"/{cmd.name} 没有 handler"
        try:
            _run(cmd.handler, ctx)
        except Exception as e:  # noqa: BLE001
            pytest.fail(f"/{cmd.name} 空参执行崩溃: {e!r}")


def test_permission_switch_uses_set_permission_mode(tmp_path: Path) -> None:
    """/permission bypass 必须经 set_permission_mode（同步 checker）并刷新状态栏。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    cmd = registry.find("permission")
    # camelCase 值 + 别名 + 大小写容错都应解析成功
    for token in ("bypass", "bypassPermissions", "BYPASS", "acceptEdits", "accept_edits", "dontAsk"):
        agent.set_permission_mode_calls.clear()
        ctx.args = token
        _run(cmd.handler, ctx)
        assert agent.set_permission_mode_calls, f"/permission {token} 未调用 set_permission_mode"
    # plan 走 set_plan_mode
    ctx.args = "plan"
    _run(cmd.handler, ctx)
    assert ui.plan_mode_calls and ui.plan_mode_calls[-1] is True
    # 无效模式给出提示、不崩
    ctx.args = "nonsense-mode"
    _run(cmd.handler, ctx)
    assert any("未知模式" in m for m in ui.messages)


def test_tools_and_status_count_enabled(tmp_path: Path) -> None:
    """/tools 与 /status 必须把 Tool 对象正确转成名字再判 is_enabled（否则恒 0）。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    ui.messages.clear()
    _run(registry.find("tools").handler, ctx)
    joined = "\n".join(ui.messages)
    # 至少有一个工具被标记为已启用（✓），不能是 0 个
    assert "✓" in joined
    assert "0 个已启用" not in joined


def test_mcp_no_crash_on_tool_objects(tmp_path: Path) -> None:
    """/mcp 之前对 Tool 对象调 .startswith 会崩——确保现在不崩。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    ui.messages.clear()
    _run(registry.find("mcp").handler, ctx)
    assert ui.messages  # 有输出即可（无 MCP 工具时提示未检测到）


def test_cancel_cancels_ui_task(tmp_path: Path) -> None:
    """/cancel 必须取消挂在 UI 上的任务（之前查 agent 上的、永远取不到）。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    task = _FakeTask(done=False)
    ui._agent_task = task
    _run(registry.find("cancel").handler, ctx)
    assert task.cancelled is True


def test_memory_clear_no_save_attr(tmp_path: Path) -> None:
    """/memory clear 之前调不存在的 save() 会崩——现在走 clear()。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    # 先写入一些记忆
    (tmp_path / ".xhx").mkdir(exist_ok=True)
    ctx.memory_manager._save_memories("- 记住这件事")
    ctx.args = "clear"
    _run(registry.find("memory").handler, ctx)
    assert ctx.memory_manager.load().strip() == ""


def test_review_triggers_agent_run(tmp_path: Path) -> None:
    """/review 必须真正驱动 agent（send_user_message），而非只塞历史。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    ctx.args = "安全性"
    _run(registry.find("review").handler, ctx)
    assert ui.sent and "安全性" in ui.sent[-1]


def test_exit_calls_graceful_exit(tmp_path: Path) -> None:
    """/exit 必须真正触发 graceful_exit（之前只设标志、界面卡住）。"""
    ctx, ui, agent, registry = _build_ctx(tmp_path)
    _run(registry.find("exit").handler, ctx)
    assert ui.graceful_called is True
