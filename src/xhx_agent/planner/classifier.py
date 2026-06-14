"""意图分类器：用关键词启发式把任务粗分到 direct / research-only / linear-edit。

仅在没有显式 --mode 时用于挑编排器。是轻量关键词规则（中英双语）、不是 LLM 分类；
判定顺序从特殊到一般：先认直达命令，再问答/研究，最后按有无写关键词落到编辑或研究。
"""

from __future__ import annotations

import re

from xhx_agent.planner.modes import ExecutionMode
from xhx_agent.repo_intel.scanner import ProjectScan


class ModeClassifier:
    def __init__(self) -> None:
        pass

    def classify(self, task: str, scan: ProjectScan | None = None) -> ExecutionMode:
        """按关键词把任务分到一个 ExecutionMode（顺序从特殊到一般，先命中先返回）。"""
        lowered = task.lower().strip()

        # 1. 直达命令（退出/清屏/帮助等）
        if lowered in {"exit", "quit", "clear", "help", "/help", "/exit", "/clear", "退出", "清屏", "帮助"}:
            return ExecutionMode.DIRECT

        # 2. 纯问答 / 解释，且不含写操作关键词
        direct_keywords = {
            r"\bexplain how\b",
            r"\bwhat is\b",
            r"\bwhy does\b",
            r"\bhello\b",
            r"\bhi\b",
            r"\bhow are you\b",
        }
        zh_direct_keywords = {"解释", "什么是", "为什么", "你好", "继续"}
        has_direct = any(re.search(kw, lowered) for kw in direct_keywords) or any(
            kw in lowered for kw in zh_direct_keywords
        )
        if has_direct and not self._has_write_keywords(lowered):
            return ExecutionMode.DIRECT

        # 3. 只读研究（分析/查找/读取…）
        research_keywords = {"analyze", "find", "search", "read", "lookup", "explain", "where", "list"}
        zh_research_keywords = {"分析", "查找", "搜索", "读取", "定位", "列表"}
        has_research = any(kw in lowered for kw in research_keywords) or any(
            kw in lowered for kw in zh_research_keywords
        )
        if has_research and not self._has_write_keywords(lowered):
            return ExecutionMode.RESEARCH_ONLY

        # 4. 兜底：有写关键词走 linear-edit，否则 research-only
        if self._has_write_keywords(lowered):
            return ExecutionMode.LINEAR_EDIT

        return ExecutionMode.RESEARCH_ONLY

    def _has_write_keywords(self, text: str) -> bool:
        write_keywords = {
            r"\bfix\b",
            r"\bpatch\b",
            r"\bmodify\b",
            r"\bdelete\b",
            r"\bwrite\b",
            r"\bupdate\b",
            r"\bimplement\b",
            r"\bcreate\b",
            r"\brefactor\b",
            r"\bintegrate\b",
        }
        zh_write_keywords = {"修复", "补丁", "修改", "删除", "写入", "更新", "实现", "创建", "继续推进", "重构", "集成"}
        return any(re.search(kw, text) for kw in write_keywords) or any(kw in text for kw in zh_write_keywords)
