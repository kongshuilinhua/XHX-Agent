"""粘贴路径回归：Ctrl+V 应从系统剪贴板插入文本。

Textual 默认的 action_paste 读 app 内部剪贴板（不含 OS 剪贴板），Windows 上 Ctrl+V
因此粘不进东西。ChatInput.action_paste 改为读系统剪贴板（read_clipboard），这里覆盖
read_clipboard 的失败兜底，以及 action_paste 的"有内容插入 / 无内容回退父类"两条路径。
"""

from __future__ import annotations

import asyncio

from xhx_agent.config import ProviderConfig
from xhx_agent.tui import clipboard


def test_read_clipboard_swallows_command_failure(monkeypatch) -> None:
    """外部剪贴板命令缺失/报错时，read_clipboard 返回 "" 而非抛异常。"""

    def _boom(*a, **k):
        raise OSError("no such command")

    monkeypatch.setattr(clipboard.subprocess, "run", _boom)
    monkeypatch.setattr(clipboard.sys, "platform", "linux")
    assert clipboard.read_clipboard() == ""


def test_read_command_nonzero_returncode(monkeypatch) -> None:
    """命令返回非 0 时视为读取失败。"""

    class _Result:
        returncode = 1
        stdout = "garbage"

    monkeypatch.setattr(clipboard.subprocess, "run", lambda *a, **k: _Result())
    assert clipboard._read_command(["whatever"]) == ""


def test_action_paste_inserts_os_clipboard(tmp_path, monkeypatch) -> None:
    """action_paste 把系统剪贴板内容插入输入框；多行内容不会触发提交。"""
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    import xhx_agent.tui.app as app_mod
    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> str:
        app = XHXApp(providers=[provider])
        async with app.run_test() as pilot:
            await pilot.pause()
            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.focus()
            monkeypatch.setattr(app_mod, "read_clipboard", lambda: "粘贴的多行\n第二行")
            chat_input.action_paste()
            await pilot.pause()
            return chat_input.text

    text = asyncio.run(_run())
    assert text == "粘贴的多行\n第二行"


def test_action_paste_falls_back_when_empty(tmp_path, monkeypatch) -> None:
    """系统剪贴板读不到内容时回退父类行为（用 app 内部剪贴板），不报错。"""
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    import xhx_agent.tui.app as app_mod
    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> str:
        app = XHXApp(providers=[provider])
        async with app.run_test() as pilot:
            await pilot.pause()
            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.focus()
            app._clipboard = "内部剪贴板"  # Textual App.clipboard 的底层字段
            monkeypatch.setattr(app_mod, "read_clipboard", lambda: "")
            chat_input.action_paste()
            await pilot.pause()
            return chat_input.text

    text = asyncio.run(_run())
    assert text == "内部剪贴板"
