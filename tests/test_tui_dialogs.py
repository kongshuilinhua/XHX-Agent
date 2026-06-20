"""TUI 内联弹窗的交互单测：用 Textual run_test pilot 驱动按键，覆盖
光标导航 / 输入 / 选择 / 提交等 action 与 on_key 分支。"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from textual.app import App, ComposeResult

from xhx_agent.memory import SessionMeta
from xhx_agent.tui.askuser_dialog import InlineAskUserWidget
from xhx_agent.tui.permission_dialog import InlinePermissionWidget
from xhx_agent.tui.plan_dialog import InlinePlanWidget
from xhx_agent.tui.session_dialog import InlineResumeWidget


class _Harness(App):
    def __init__(self, widget) -> None:
        super().__init__()
        self._w = widget
        self.responses: list = []

    def compose(self) -> ComposeResult:
        yield self._w

    # 捕获四种弹窗的回传消息
    def on_inline_plan_widget_responded(self, e) -> None:
        self.responses.append(e)

    def on_inline_resume_widget_selected(self, e) -> None:
        self.responses.append(e)

    def on_inline_permission_widget_responded(self, e) -> None:
        self.responses.append(e)

    def on_inline_ask_user_widget_responded(self, e) -> None:
        self.responses.append(e)


def test_plan_dialog_keys() -> None:
    w = InlinePlanWidget()

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")  # 0 → 1
            assert w._cursor == 1
            await pilot.press("3")  # 进入反馈输入
            assert w._cursor == 2
            await pilot.press("o", "k")  # 输入反馈
            assert w._input == "ok"
            await pilot.press("backspace")
            assert w._input == "o"
            await pilot.press("shift+tab")  # 带反馈批准
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses and responses[-1].feedback == "o"


def test_plan_dialog_number_select() -> None:
    w = InlinePlanWidget()

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("1")  # 直接 YOLO
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses  # 数字键 1 触发了回传


def test_permission_dialog_keys() -> None:
    w = InlinePermissionWidget("Bash", "rm -rf /tmp/x")

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            assert w._cursor == 1
            await pilot.press("up")
            assert w._cursor == 0
            await pilot.press("enter")  # 选 Yes → ALLOW
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses


def test_resume_dialog_keys() -> None:
    metas = [
        SessionMeta(session_id="s1", title="贪吃蛇", last_active=datetime.now(UTC), message_count=5, file_size=1024),
        SessionMeta(session_id="s2", title="图书系统", last_active=datetime.now(UTC), message_count=9, file_size=2048),
    ]
    w = InlineResumeWidget(metas, project_name="proj")

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")
            assert w._cursor == 1
            await pilot.press("图")  # 搜索 → 过滤
            await pilot.press("backspace")
            await pilot.press("enter")  # 选择
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses


def test_askuser_single_question() -> None:
    q = [{"name": "color", "question": "选颜色", "options": [{"label": "红"}, {"label": "蓝"}]}]
    w = InlineAskUserWidget(q)

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")  # 选第二项
            await pilot.press("enter")  # 单问题 → 直接提交
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses and responses[-1].answers["color"] == "蓝"


def test_askuser_multi_and_other() -> None:
    qs = [
        {"name": "a", "question": "Q1", "options": [{"label": "x"}, {"label": "y"}], "multiSelect": True},
        {"name": "b", "question": "Q2", "options": [{"label": "z"}]},
    ]
    w = InlineAskUserWidget(qs)

    async def _run():
        h = _Harness(w)
        async with h.run_test() as pilot:
            await pilot.pause()
            await pilot.press("space")  # 勾选 x
            await pilot.press("tab")  # 下一题
            assert w._q_idx == 1
            # 移动到 Other 并输入
            await pilot.press("down")  # cursor 到 Other
            await pilot.press("h", "i")
            await pilot.press("tab")  # → 提交视图
            assert w._on_submit is True
            await pilot.press("enter")  # 提交
            await pilot.pause()
        return h.responses

    responses = asyncio.run(_run())
    assert responses and responses[-1].answers is not None
