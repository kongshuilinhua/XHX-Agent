"""agents/task_manager.py 单测：后台任务 launch / 完成 / 取消 / 查询。"""

from __future__ import annotations

import asyncio
from typing import Any

from xhx_agent.agents.task_manager import TaskManager


class _FakeAgent:
    def __init__(self, sleep: float = 0.0) -> None:
        self.team_name = ""
        self._team_manager = None
        self.total_input_tokens = 7
        self.total_output_tokens = 3
        self.agent_id = "a1"
        self._sleep = sleep

    async def run_to_completion(self, task: str, conv: Any = None) -> str:
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return f"done: {task}"


def test_launch_completes_and_query() -> None:
    async def _run() -> None:
        tm = TaskManager()
        tid = tm.launch(_FakeAgent(), "build snake", name="worker")
        task = tm._async_tasks[tid]
        await asyncio.wait_for(task, timeout=5)

        bg = tm.get(tid)
        assert bg is not None
        assert bg.status == "completed"
        assert bg.result == "done: build snake"
        assert bg.progress.input_tokens == 7

        # poll_completed 返回完成的任务
        completed = tm.poll_completed()
        assert any(b.id == tid for b in completed)
        # list_tasks 包含它
        assert any(b.id == tid for b in tm.list_tasks())
        # 已完成任务无法再取消
        assert tm.cancel(tid) is False

    asyncio.run(_run())


def test_cancel_running_task() -> None:
    async def _run() -> None:
        tm = TaskManager()
        tid = tm.launch(_FakeAgent(sleep=10), "long job")
        await asyncio.sleep(0.05)  # 让任务开始
        assert tm.cancel(tid) is True
        task = tm._async_tasks.get(tid)
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=2)
            except (asyncio.CancelledError, Exception):
                pass
        bg = tm.get(tid)
        assert bg is not None and bg.status == "cancelled"

    asyncio.run(_run())


def test_get_and_cancel_missing() -> None:
    tm = TaskManager()
    assert tm.get("nope") is None
    assert tm.cancel("nope") is False
    assert tm.list_tasks() == []
    assert tm.poll_completed() == []


def test_adopt_running() -> None:
    async def _run() -> None:
        tm = TaskManager()
        tid = tm.adopt_running(_FakeAgent(), "resumed task", partial_result="前半段")
        task = tm._async_tasks[tid]
        await asyncio.wait_for(task, timeout=5)
        bg = tm.get(tid)
        assert bg is not None and bg.status == "completed"
        assert "前半段" in bg.result and "done: resumed task" in bg.result

    asyncio.run(_run())
