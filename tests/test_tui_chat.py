"""TUI 端到端对话冒烟：用 mock provider 真实驱动 XHXApp 提交消息，跑完整消息处理器。

覆盖之前漏测的交互路径（call_ui / session.meta / LoopComplete / 会话摘要）——
这些只在真实 TUI 事件流里执行，agent.run() 直接调用走不到。
"""

from __future__ import annotations

import asyncio

from xhx_agent.config import ProviderConfig


def test_tui_chat_roundtrip_no_crash(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)

    from xhx_agent.tui.app import ChatInput, XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> list[str]:
        app = XHXApp(providers=[provider])
        errors: list[str] = []
        async with app.run_test() as pilot:
            # 捕获任何被兜底显示的错误（含 AttributeError 等）。
            monkeypatch.setattr(app, "_show_error", lambda msg: errors.append(str(msg)))
            await pilot.pause()

            chat_input = app.query_one("#chat-input", ChatInput)
            chat_input.post_message(ChatInput.Submitted("你好"))

            # 等消息处理器（含 agent 流式 + LoopComplete 收尾）跑完。
            for _ in range(200):
                await pilot.pause(0.05)
                task = getattr(app, "_agent_task", None)
                if task is not None and task.done() and not app._streaming:
                    break
        return errors

    errors = asyncio.run(_run())

    assert errors == [], f"TUI 对话路径报错: {errors}"
