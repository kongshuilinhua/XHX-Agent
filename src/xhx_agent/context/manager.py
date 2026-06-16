"""上下文管理器：工具结果体积控制 + 对话压缩 + 熔断 + 恢复。

来源：mewcode context/manager.py，适配 XHX-Agent 的 dict 消息格式。

与 compiler.py 的关系：
    compiler.py → 前置编译：每次模型调用前精选上下文
    manager.py → 后置管理：工具结果体积控制 + 对话压缩
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SINGLE_RESULT_CHAR_LIMIT = 50_000
AGGREGATE_CHAR_LIMIT = 200_000
PREVIEW_CHARS = 2_000

KEEP_RECENT_TURNS = 10
KEEP_RECENT_TOKENS = 10_000
MIN_KEEP_MESSAGES = 5
KEEP_MAX_TOKENS = 40_000

MIN_SUMMARIZE_PREFIX_TOKENS = 2_000

SUMMARY_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_SAFETY_MARGIN = 13_000
MANUAL_COMPACT_SAFETY_MARGIN = 3_000

RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
_RECOVERY_CHARS_PER_TOKEN = 3.5

SNIPPED_TAG = "<snipped>"
PERSISTED_TAG = "<persisted-output>"


# ---------------------------------------------------------------------------
# ContentReplacementState — Design B（决策冻结，不做原地修改）
# ---------------------------------------------------------------------------


@dataclass
class ContentReplacementState:
    """记录哪些 tool_result 已被替换/已见。

    核心思想（Design B）：原始 conversation 数组永远不变，
    只在 API 调用时根据本表生成替换视图。这样 prompt cache 不会因内容替换失效。
    """
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass
class ContentReplacementRecord:
    """单条替换记录，可持久化到 JSONL。"""
    tool_use_id: str
    replacement: str
    kind: str = "tool-result"


def create_replacement_state() -> ContentReplacementState:
    return ContentReplacementState()


def clone_replacement_state(src: ContentReplacementState) -> ContentReplacementState:
    return ContentReplacementState(
        seen_ids=set(src.seen_ids),
        replacements=dict(src.replacements),
    )


# ---------------------------------------------------------------------------
# 恢复状态 — 压缩后重建工作上下文
# ---------------------------------------------------------------------------


@dataclass
class FileReadRecord:
    """记录一次文件读取，压缩后可重新附加到摘要消息上。"""
    path: str
    content: str
    timestamp: float


@dataclass
class SkillInvocationRecord:
    """记录一次 skill 调用，压缩后可重新附加 SOP 正文。"""
    name: str
    body: str
    timestamp: float


class RecoveryState:
    """跨压缩存活的工作上下文快照。

    记录 ReadFile 返回的内容和 skill SOP 正文，压缩后重新附加到
    摘要 user 消息上，让模型保有可用的工作上下文。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(
                path=path, content=content, timestamp=time.time(),
            )

    def record_skill_invocation(self, name: str, body: str) -> None:
        if not name:
            return
        with self._lock:
            self._skills[name] = SkillInvocationRecord(
                name=name, body=body, timestamp=time.time(),
            )

    def snapshot_files(self, limit: int | None = None) -> list[FileReadRecord]:
        """返回最近读取的文件快照，按时间倒序。"""
        if limit is None:
            limit = RECOVERY_FILE_LIMIT
        with self._lock:
            records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        if limit > 0:
            records = records[:limit]
        return records

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        """返回最近调用的 skill 快照，按时间倒序。"""
        with self._lock:
            records = list(self._skills.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records

    def clear(self) -> None:
        with self._lock:
            self._files.clear()
            self._skills.clear()


# ---------------------------------------------------------------------------
# 熔断器
# ---------------------------------------------------------------------------


@dataclass
class CompactCircuitBreaker:
    """连续压缩失败 N 次后自动熔断，防止死循环重试。"""

    max_failures: int = 3
    consecutive_failures: int = field(default=0, init=False)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def is_open(self) -> bool:
        """熔断器是否已断开（不应再尝试压缩）。"""
        return self.consecutive_failures >= self.max_failures

    def reset(self) -> None:
        self.consecutive_failures = 0


# ---------------------------------------------------------------------------
# 摘要 Prompt
# ---------------------------------------------------------------------------

SUMMARY_PROMPT = """\
你是一个对话摘要助手。你只能输出纯文本，不能调用任何工具。

请对下面的对话生成一份结构化摘要。

先在 <analysis> 标签中梳理对话中发生了什么（这部分会被丢弃），然后在 <summary> 标签中输出正式摘要。

<summary> 必须包含以下 9 个部分：

1. **主要请求和意图**：用户到底想做什么
2. **关键技术概念**：讨论过的重要技术点
3. **文件和代码段**：涉及哪些文件，关键代码片段要保留
4. **错误和修复**：遇到了什么错，怎么解决的
5. **问题解决过程**：解决问题的思路和方法
6. **所有用户消息**：用户说过的所有非工具结果的话（原文保留，不可改写！）
7. **待办任务**：还没完成的事
8. **当前工作**：最近在做什么（要最详细）
9. **可能的下一步**：接下来打算做什么

提醒：不要调用任何工具。工具调用会被拒绝，你会失败。只输出纯文本。"""


def extract_summary(llm_output: str) -> str:
    """从 LLM 输出中提取 <summary> 标签内容。"""
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start == -1 or end == -1:
        return llm_output
    return llm_output[start + len("<summary>"):end].strip()


# ---------------------------------------------------------------------------
# Token 估算辅助
# ---------------------------------------------------------------------------


def _approx_tokens(s: str) -> int:
    """字符级启发式 token 估算。"""
    if not s:
        return 0
    return int(len(s) / _RECOVERY_CHARS_PER_TOKEN)


def _truncate_by_tokens(s: str, token_budget: int) -> str:
    """按 token 预算截断字符串。"""
    if token_budget <= 0 or not s:
        return s
    if _approx_tokens(s) <= token_budget:
        return s
    max_chars = int(token_budget * _RECOVERY_CHARS_PER_TOKEN)
    if max_chars <= 0 or max_chars >= len(s):
        return s
    return s[:max_chars] + "\n… (内容已截断)"


# ---------------------------------------------------------------------------
# 压缩后恢复附件构建
# ---------------------------------------------------------------------------


def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: list[dict[str, Any]] | None = None,
) -> str:
    """渲染压缩后附件：最近文件 + 已激活 skill + 可用工具 + 提示。

    没有任何值得附加的内容时返回空字符串。
    """
    sections: list[str] = []

    if state is not None:
        # ── 最近读过的文件 ──
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            buf = [
                "## 最近读过的文件\n",
                "以下快照是文件读取工具上次返回的内容。如需当前字节请重新读取。\n",
            ]
            for rec in files:
                content = _truncate_by_tokens(rec.content, RECOVERY_TOKENS_PER_FILE)
                ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(rec.timestamp))
                buf.append(f"### {rec.path}  (read {ts})\n")
                buf.append("```\n")
                buf.append(content)
                if not content.endswith("\n"):
                    buf.append("\n")
                buf.append("```\n")
            sections.append("".join(buf))

        # ── 已激活的技能 ──
        skills = state.snapshot_skills()
        if skills:
            buf = [
                "## 已激活的技能\n",
                "下列技能在本会话中被调用过，其触发条件仍然适用。\n",
            ]
            used = 0
            emitted = False
            for sk in skills:
                body = _truncate_by_tokens(sk.body, RECOVERY_TOKENS_PER_SKILL)
                tokens = _approx_tokens(body) + _approx_tokens(sk.name) + 8
                if used + tokens > RECOVERY_SKILLS_BUDGET:
                    break
                used += tokens
                buf.append(f"### {sk.name}\n\n{body}\n")
                emitted = True
            if emitted:
                sections.append("".join(buf))

    # ── 可用工具 ──
    if tool_schemas:
        buf = [
            "## 可用工具\n",
            "你仍然可以调用以下工具，需要时直接发起调用即可：\n",
        ]
        for t in tool_schemas:
            name = t.get("name", "")
            if not name:
                continue
            desc = t.get("description", "")
            first_line = desc.split("\n")[0].strip() if desc else ""
            if first_line:
                buf.append(f"- {name} — {first_line}\n")
            else:
                buf.append(f"- {name}\n")
        sections.append("".join(buf))

    if not sections:
        return ""

    sections.append(
        "## 提示\n\n以上恢复的上下文是重建的。若需要原文代码、错误信息或用户原话，"
        "请用文件读取工具重新读取，不要根据摘要猜测细节。\n"
    )
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Context Window 阈值计算
# ---------------------------------------------------------------------------


def compute_compact_threshold(context_window: int, manual: bool = False) -> int:
    """计算应该触发压缩的 token 阈值。"""
    effective = context_window - SUMMARY_OUTPUT_RESERVE
    margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
    return effective - margin


def should_auto_compact(last_input_tokens: int, context_window: int) -> bool:
    """判断是否应该自动触发压缩。"""
    return last_input_tokens >= compute_compact_threshold(context_window)
