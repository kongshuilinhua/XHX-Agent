"""命令子命令回归：worktree create/enter/exit/status、tasks info/cancel、
session delete、memory edit。

这些子命令此前被裁成"只剩 list"。本测试用真 WorktreeManager / SessionManager /
MemoryManager（tasks 用持有真 BackgroundTask 的轻量 manager）验证每个子命令真正
干活，而不是只打印占位。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

from xhx_agent.commands import CommandContext


class _FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    async def show_resume_picker(self) -> None:
        self.messages.append("__RESUME_PICKER__")


class _FakeAgent:
    def __init__(self, work_dir: str) -> None:
        self.work_dir = work_dir


def _git_init(d: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
    (d / "a.txt").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=d, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=d, check=True)


def test_worktree_create_status_exit(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _git_init(tmp_path)
    from xhx_agent.commands.handlers.worktree import create_worktree_command
    from xhx_agent.worktree.manager import WorktreeManager

    wm = WorktreeManager(project_root=str(tmp_path))
    cmd = create_worktree_command(wm)
    ui = _FakeUI()
    agent = _FakeAgent(str(tmp_path))

    def ctx(args: str) -> CommandContext:
        return CommandContext(args=args, agent=agent, ui=ui)

    async def run() -> None:
        await cmd.handler(ctx("create feat-a"))
        assert any("已创建并进入" in m for m in ui.messages)
        assert len(wm.list_worktrees()) == 1
        assert agent.work_dir != str(tmp_path)  # work_dir 真切到 worktree

        ui.messages.clear()
        await cmd.handler(ctx("status"))
        assert any("feat-a" in m and "Worktree 状态" in m for m in ui.messages)

        ui.messages.clear()
        await cmd.handler(ctx("exit"))
        assert any("已退出 worktree" in m for m in ui.messages)
        assert len(wm.list_worktrees()) == 0

    asyncio.run(run())


def test_tasks_info_and_cancel() -> None:
    from xhx_agent.agents.task_manager import BackgroundTask
    from xhx_agent.commands.handlers.tasks import create_tasks_command

    bg = BackgroundTask(id="abc12345", agent=None, name="子任务", task="do x", status="running", result="结果文本")

    class _Mgr:
        def __init__(self) -> None:
            self._t = {bg.id: bg}

        def list_tasks(self) -> list[Any]:
            return list(self._t.values())

        def get(self, tid: str) -> Any:
            return self._t.get(tid)

        def cancel(self, tid: str) -> bool:
            t = self._t.get(tid)
            if t is not None and t.status == "running":
                t.status = "cancelled"
                return True
            return False

    cmd = create_tasks_command(_Mgr())
    ui = _FakeUI()

    async def run() -> None:
        await cmd.handler(CommandContext(args="info abc12345", ui=ui))
        assert any("任务详情" in m and "结果文本" in m for m in ui.messages)

        ui.messages.clear()
        await cmd.handler(CommandContext(args="cancel abc12345", ui=ui))
        assert any("已取消任务" in m for m in ui.messages)

        ui.messages.clear()
        await cmd.handler(CommandContext(args="cancel abc12345", ui=ui))
        assert any("无法取消" in m for m in ui.messages)  # 已 cancelled，再取消失败

        ui.messages.clear()
        await cmd.handler(CommandContext(args="info nope", ui=ui))
        assert any("未找到任务" in m for m in ui.messages)

    asyncio.run(run())


def test_session_delete(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.commands.handlers.session import handle_session
    from xhx_agent.conversation import Message
    from xhx_agent.memory import SessionManager

    sm = SessionManager(str(tmp_path))
    sess = sm.create()
    sess.append(Message(role="user", content="hi"))  # 触发 jsonl 落盘
    sid = sess.session_id
    jsonl = tmp_path / ".xhx" / "sessions" / f"{sid}.jsonl"
    assert jsonl.exists()

    ui = _FakeUI()

    async def run() -> None:
        await handle_session(CommandContext(args=f"delete {sid}", session_manager=sm, ui=ui))
        assert any("已删除会话" in m for m in ui.messages)
        assert not jsonl.exists()

        ui.messages.clear()
        await handle_session(CommandContext(args="delete nonexistent", session_manager=sm, ui=ui))
        assert any("未找到会话" in m for m in ui.messages)

    asyncio.run(run())


def test_memory_edit_shows_paths(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.commands.handlers.memory import handle_memory
    from xhx_agent.memory.auto_memory import MemoryManager

    ui = _FakeUI()

    async def run() -> None:
        await handle_memory(CommandContext(args="edit", memory_manager=MemoryManager(str(tmp_path)), ui=ui))
        joined = "\n".join(ui.messages)
        assert "编辑记忆文件" in joined and "用户级" in joined and "项目级" in joined

    asyncio.run(run())
