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


class SessionMeta:
    """会话元数据（占位）：提供 TUI 访问的字段与无操作的持久化接口。"""

    def __init__(self) -> None:
        self.total_tokens = 0
        self.summary = ""

    def save(self, path: object) -> None:
        # 会话元数据持久化尚未实现，安全无操作（不影响对话流程）。
        return None


class Session:
    """会话句柄（占位）：提供 TUI 所需的全部属性/方法，均为不崩的安全实现。"""

    def __init__(self, session_id: str = "", sessions_dir: object = None, **kwargs: object) -> None:
        from pathlib import Path

        self.session_id = session_id or "session-0"
        self._sessions_dir = Path(str(sessions_dir)) if sessions_dir else Path(".xhx") / "sessions"
        self.meta = SessionMeta()

    def append(self, msg: object) -> None:
        pass

    def append_record(self, record: object) -> None:
        pass

    def close(self) -> None:
        pass


class SessionManager:
    """会话管理器（占位）。"""

    def __init__(self, work_dir: str = "") -> None:
        from pathlib import Path

        self._work_dir = work_dir
        self._sessions_dir = (Path(work_dir) if work_dir else Path(".")) / ".xhx" / "sessions"

    def create(self) -> Session:
        import uuid

        try:
            self._sessions_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return Session(session_id=uuid.uuid4().hex[:12], sessions_dir=self._sessions_dir)

    def cleanup(self) -> None:
        pass

    def list_sessions(self) -> list[Session]:
        return []


async def generate_session_summary(*args: object, **kwargs: object) -> str:
    """生成会话摘要（占位）：异步签名以匹配调用方 await，当前返回空串。"""
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
