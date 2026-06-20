"""TUI app 深层流程冒烟：计划审批流 + 带工具的对话流，驱动 app.py 的
事件分发、工具块渲染、计划弹窗与执行路径。"""

from __future__ import annotations

import asyncio

from xhx_agent.config import ProviderConfig


def _provider() -> ProviderConfig:
    return ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")


def _boot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)


def test_plan_approval_flow(tmp_path, monkeypatch) -> None:
    _boot(tmp_path, monkeypatch)
    from xhx_agent.tui.app import ChatInput, XHXApp

    async def _run() -> list[str]:
        app = XHXApp(providers=[_provider()])
        errors: list[str] = []
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "_show_error", lambda m: errors.append(str(m)))
            await pilot.pause()
            app.query_one("#chat-input", ChatInput).post_message(ChatInput.Submitted("帮我做个计划"))

            # 等计划审批弹窗出现（mock 会调用 present_plan）
            appeared = False
            for _ in range(120):
                await pilot.pause(0.05)
                if app.query("#plan-inline"):
                    appeared = True
                    break
            if appeared:
                await pilot.press("1")  # YOLO 批准 → 触发执行
                for _ in range(120):
                    await pilot.pause(0.05)
                    task = getattr(app, "_agent_task", None)
                    if task is not None and task.done() and not app._streaming:
                        break
        return errors

    errors = asyncio.run(_run())
    assert errors == [], f"计划审批流报错: {errors}"


def test_tool_using_chat_flow(tmp_path, monkeypatch) -> None:
    _boot(tmp_path, monkeypatch)
    from xhx_agent.tui.app import ChatInput, XHXApp

    async def _run() -> list[str]:
        app = XHXApp(providers=[_provider()])
        errors: list[str] = []
        async with app.run_test() as pilot:
            monkeypatch.setattr(app, "_show_error", lambda m: errors.append(str(m)))
            await pilot.pause()
            # bypass 模式避免权限弹窗阻塞；"修" 触发 mock 走 read_file 工具
            app.query_one("#chat-input", ChatInput).post_message(ChatInput.Submitted("/permission bypassPermissions"))
            await pilot.pause(0.05)
            app.query_one("#chat-input", ChatInput).post_message(ChatInput.Submitted("修复 README 里的错别字"))
            for _ in range(160):
                await pilot.pause(0.05)
                task = getattr(app, "_agent_task", None)
                if task is not None and task.done() and not app._streaming:
                    break
        return errors

    errors = asyncio.run(_run())
    assert errors == [], f"工具对话流报错: {errors}"
