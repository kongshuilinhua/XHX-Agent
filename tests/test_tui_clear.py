"""Ctrl+U 一键清空输入框回归。

基类 TextArea 的 ctrl+u 只删当前行光标前的部分；ChatInput 覆盖为清空整个框
（含多行草稿），并复位历史导航状态。这里覆盖多行草稿 + 光标在中间的场景，
确认整框被清空。
"""

from __future__ import annotations

import asyncio

from xhx_agent.config import ProviderConfig


def test_ctrl_u_clears_whole_input(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> tuple[str, int]:
        app = XHXApp(providers=[provider])
        async with app.run_test() as pilot:
            await pilot.pause()
            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.focus()
            chat_input.insert("第一行\n第二行")
            # 把光标移到开头，确认清空与光标位置无关（基类 ctrl+u 此时会一字不删）。
            chat_input.move_cursor((0, 0))
            chat_input._history_index = 3  # 模拟正在翻历史
            chat_input.action_clear_input()
            await pilot.pause()
            return chat_input.text, chat_input._history_index

    text, history_index = asyncio.run(_run())
    assert text == ""
    assert history_index == -1  # 历史导航状态已复位
