"""memory/auto_memory.py 单测：记忆提取 extract。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from xhx_agent.memory.auto_memory import MemoryManager


def test_extract_force_saves(tmp_path: Path) -> None:
    mgr = MemoryManager(str(tmp_path))

    async def fake_summarize(prompt: str) -> str:
        assert "对话内容" in prompt  # prompt 含对话
        return "- 用户偏好简洁回复"

    msgs = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好"}]
    result = asyncio.run(mgr.extract(msgs, fake_summarize, force=True))
    assert result == "- 用户偏好简洁回复"
    assert "简洁" in mgr.load()


def test_extract_skips_below_interval(tmp_path: Path) -> None:
    mgr = MemoryManager(str(tmp_path))

    async def fake_summarize(prompt: str) -> str:
        return "x"

    # 非 force 且消息太少 → 跳过返回 None
    result = asyncio.run(mgr.extract([{"role": "user", "content": "a"}], fake_summarize, force=False))
    assert result is None


def test_extract_swallows_summarize_error(tmp_path: Path) -> None:
    mgr = MemoryManager(str(tmp_path))

    async def boom(prompt: str) -> str:
        raise RuntimeError("llm down")

    result = asyncio.run(mgr.extract([{"role": "user", "content": "a"}], boom, force=True))
    assert result is None


def test_clear(tmp_path: Path) -> None:
    mgr = MemoryManager(str(tmp_path))
    mgr._save_memories("- 记住这个")
    assert mgr.load().strip() != ""
    assert mgr.clear() is True
    assert mgr.load().strip() == ""
    # 再次清空（已空文件仍存在）→ True
    assert mgr.clear() is True
