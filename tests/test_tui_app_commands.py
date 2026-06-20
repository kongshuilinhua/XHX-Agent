"""TUI app 级冒烟：用 mock provider 真实启动 XHXApp，逐条提交 slash 命令，
驱动 _dispatch_command + 各 handler + 状态栏刷新，断言无兜底错误。"""

from __future__ import annotations

import asyncio

from xhx_agent.config import ProviderConfig


def test_slash_commands_no_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    commands = [
        "/help",
        "/help permission",
        "/",  # 列出全部命令
        "/status",
        "/tools",
        "/mcp",
        "/memory",
        "/memory clear",
        "/permission",  # 显示当前 + 可用
        "/permission bypassPermissions",
        "/permission default",
        "/plan",  # 进入
        "/plan",  # 退出
        "/verbose",
        "/model",
        "/skill",
        "/clear",
        "/session",  # 无历史 → 提示
        "/compact",
        "/rewind 1",
        "/worktree",
        "/tasks",
        "/trace",
        "/unknown_cmd",  # 未知 → 系统提示（非错误）
        "/cancel",
    ]

    async def _run() -> list[str]:
        app = XHXApp(providers=[provider])
        errors: list[str] = []
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "_show_error", lambda msg: errors.append(str(msg)))
            await pilot.pause()
            chat_input = app.query_one("#chat-input", ChatInput)
            for cmd in commands:
                chat_input.post_message(ChatInput.Submitted(cmd))
                await pilot.pause(0.02)
            await pilot.pause(0.1)
        return errors

    errors = asyncio.run(_run())
    assert errors == [], f"slash 命令触发兜底错误: {errors}"


def test_mode_cycle_shift_tab(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    from xhx_agent.tui.app import XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> None:
        app = XHXApp(providers=[provider])
        async with app.run_test() as pilot:
            await pilot.pause()
            # 循环切换权限模式（shift+tab）若干次，不应崩溃
            for _ in range(5):
                await pilot.press("shift+tab")
                await pilot.pause(0.02)

    asyncio.run(_run())
