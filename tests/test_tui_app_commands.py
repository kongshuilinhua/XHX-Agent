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


def test_send_message_after_new_session(tmp_path, monkeypatch) -> None:
    """回归：/new 之后再发普通消息不能卡死。

    曾经 /new 把 self.conversation 置成 None，_send_message 在 add_user_message 处
    AttributeError，既不回复又把 _streaming 卡在 True，下一条消息只剩 "(response interrupted)"。
    现在 /new 应新建空会话对象，消息能正常跑完一轮。
    """
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> tuple[list[str], list[str], bool, bool, bool]:
        app = XHXApp(providers=[provider])
        errors: list[str] = []
        system_msgs: list[str] = []
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "_show_error", lambda msg: errors.append(str(msg)))
            monkeypatch.setattr(app, "_show_system_message", lambda msg: system_msgs.append(str(msg)))
            await pilot.pause()

            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.post_message(ChatInput.Submitted("/new"))
            await pilot.pause(0.05)

            # /new 后会话对象必须是新建的有效对象，而非 None。
            conv_ok = app.conversation is not None
            session_ok = app.session is not None

            chat_input.post_message(ChatInput.Submitted("你好"))
            # 等流式开始再等其结束；旧 bug 下 _streaming 会卡 True 永不复位。
            for _ in range(50):
                await pilot.pause(0.02)
                if app._streaming or app._agent_task is not None:
                    break
            for _ in range(150):
                await pilot.pause(0.02)
                if not app._streaming:
                    break
            await pilot.pause(0.05)

            streaming_cleared = not app._streaming
        return errors, system_msgs, conv_ok, session_ok, streaming_cleared

    errors, system_msgs, conv_ok, session_ok, streaming_cleared = asyncio.run(_run())
    assert conv_ok, "/new 后 conversation 不应为 None"
    assert session_ok, "/new 后应新建 session 以便落盘/恢复"
    assert errors == [], f"/new 后发消息触发兜底错误: {errors}"
    assert "(response interrupted)" not in system_msgs, f"不应出现中断提示: {system_msgs}"
    assert streaming_cleared, "_streaming 卡在 True：消息没跑完一轮"


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
