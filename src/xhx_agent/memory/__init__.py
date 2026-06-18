"""长期记忆 / 跨会话上下文（Phase 6a）：上下文管理的第 ④ 轴（时间轴）。

`store` 负责落盘/索引（`.xhx/memory/` + `MEMORY.md`），`recall` 负责确定性召回并渲染成
可注入 context-pack / 编排器 system prompt 的记忆块。无 LLM 调用、可复现。
"""

from xhx_agent.memory.auto_memory import MemoryManager
from xhx_agent.memory.extract import parse_memory_candidates, propose_memories
from xhx_agent.memory.recall import find_relevant_memories, recall_memories, render_recalled_memories, render_reminder
from xhx_agent.memory.store import (
    MEMORY_TYPES,
    MemoryRecord,
    delete_memory,
    list_memories,
    memory_dir,
    parse_memory_file,
    slugify,
    write_memory,
)

# ---- 桥接：新 TUI 需要但尚未完整实现的 memory 功能 ----


class Session:
    """会话句柄（占位）。"""

    def __init__(self, session_id: str = "", **kwargs: object) -> None:
        self.session_id = session_id or "session-0"

    def append(self, msg: object) -> None:
        pass

    def close(self) -> None:
        pass


class SessionManager:
    """会话管理器（占位）。"""

    def __init__(self, work_dir: str = "") -> None:
        self._work_dir = work_dir

    def create(self) -> Session:
        import uuid

        return Session(session_id=uuid.uuid4().hex[:12])

    def cleanup(self) -> None:
        pass

    def list_sessions(self) -> list[Session]:
        return []


def generate_session_summary(messages: list, model: str = "") -> str:
    """生成会话摘要（占位）。"""
    return ""


def load_instructions(work_dir: str) -> str:
    """加载项目指令。"""
    from pathlib import Path

    p = Path(work_dir) / "XHX.md"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


def make_compact_boundary(**kwargs: object) -> dict:
    """创建压缩边界记录（占位）。"""
    return {}


__all__ = [
    "MEMORY_TYPES",
    "MemoryManager",
    "MemoryRecord",
    "Session",
    "SessionManager",
    "delete_memory",
    "find_relevant_memories",
    "generate_session_summary",
    "list_memories",
    "load_instructions",
    "make_compact_boundary",
    "memory_dir",
    "parse_memory_candidates",
    "parse_memory_file",
    "propose_memories",
    "recall_memories",
    "render_recalled_memories",
    "render_reminder",
    "slugify",
    "write_memory",
]
