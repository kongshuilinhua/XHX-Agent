"""LLM 自动记忆提取：分析对话，提取值得长期记忆的事实。

来源：mewcode memory/auto_memory.py，与 XHX-Agent 现有的确定性召回互补：
- recall.py → 确定性 token 重叠召回（快速、可复现、零成本）
- auto_memory.py → LLM 深度分析提取（语义理解、跨会话关联）

触发条件：每 N 轮自动触发 LLM 分析一次。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

USER_MEMORIES_RELPATH = ".xhx/memories.md"
PROJECT_MEMORIES_RELPATH = ".xhx/memories.md"

MEMORY_EXTRACTION_PROMPT = """\
你是一个记忆提取助手。分析下面的对话，提取值得长期记忆的信息，更新 memories.md。

分类规则：
- **用户偏好**：用户的编码习惯和风格要求（如缩进、命名规范、语言偏好）
- **纠正反馈**：用户明确指出的错误和正确做法
- **项目知识**：当前项目的具体技术信息（技术栈、目录结构、部署方式）
- **参考资料**：外部链接和文档地址

规则：
1. 已有相同含义的条目不要重复添加
2. 没有值得记忆的内容，该分类下留空（不要写任何条目，不要写占位符）
3. 每条记忆用一行 `- ` 开头，必须是具体内容
4. 输出完整的 memories.md 内容，包含所有四个分类标题

输出格式（严格遵守，没有内容的分类下不写任何条目）：
### 用户偏好
- 用户偏好简洁代码风格

### 纠正反馈

### 项目知识
- 项目使用 PostgreSQL 15

### 参考资料

不要输出任何其他内容，不要调用任何工具。"""

_USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
_PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


class MemoryManager:
    """LLM 记忆管理器：从对话中提取长期记忆。

    使用方式::

        mgr = MemoryManager(project_root="/path/to/project")
        # 每 N 轮触发一次
        if turn % 5 == 0:
            await mgr.extract(messages, summarize_fn)
    """

    def __init__(self, project_root: str) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = Path(project_root) / PROJECT_MEMORIES_RELPATH
        self._last_extraction_msg_count = 0

    @property
    def user_path(self) -> Path:
        return self._user_path

    @property
    def project_path(self) -> Path:
        return self._project_path

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        messages: list[dict[str, Any]],
        summarize_fn: Any,
        *,
        force: bool = False,
    ) -> str | None:
        """分析对话并更新 memories.md。

        Args:
            messages: 对话消息列表。
            summarize_fn: LLM 调用函数。
            force: 强制执行，忽略轮次间隔。

        Returns:
            提取的记忆文本，无新内容返回 None。
        """
        if not force and len(messages) < self._last_extraction_msg_count + 10:
            return None

        # 构建 prompt
        conv_text = self._format_conversation(messages[-20:])  # 只分析最近 20 条
        existing = self._load_existing()

        prompt = (
            f"{MEMORY_EXTRACTION_PROMPT}\n\n"
            f"## 现有记忆（请在此基础上更新）\n\n{existing}\n\n"
            f"## 对话内容\n\n{conv_text}"
        )

        try:
            result = await summarize_fn(prompt)
            if result:
                self._save_memories(result)
                self._last_extraction_msg_count = len(messages)
                return result
        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _format_conversation(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if content:
                # 截断长内容
                if len(str(content)) > 2000:
                    content = str(content)[:2000] + "... (truncated)"
                lines.append(f"**{role}**: {content}")
        return "\n\n".join(lines)

    def _load_existing(self) -> str:
        """加载现有 memory 文件（优先项目级，回退用户级）。"""
        for p in (self._project_path, self._user_path):
            if p.is_file():
                try:
                    return p.read_text(encoding="utf-8")
                except OSError:
                    pass
        return ""

    def _save_memories(self, content: str) -> None:
        """保存记忆内容。项目级记忆写入项目目录，用户偏好写入用户目录。"""
        # 全量写入项目级文件
        if content.strip():
            self._project_path.parent.mkdir(parents=True, exist_ok=True)
            self._project_path.write_text(content, encoding="utf-8")
