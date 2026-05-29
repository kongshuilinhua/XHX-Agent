from __future__ import annotations

import re

from xhx_agent.planner.modes import ExecutionMode
from xhx_agent.repo_intel.scanner import ProjectScan


class ModeClassifier:
    def __init__(self) -> None:
        pass

    def classify(self, task: str, scan: ProjectScan | None = None) -> ExecutionMode:
        lowered = task.lower().strip()

        # 1. Simple direct commands
        if lowered in {"exit", "quit", "clear", "help", "/help", "/exit", "/clear", "退出", "清屏", "帮助"}:
            return ExecutionMode.DIRECT

        # 2. Simple Q&A or explanation with no file operations
        direct_keywords = {r"\bexplain how\b", r"\bwhat is\b", r"\bwhy does\b", r"\bhello\b", r"\bhi\b", r"\bhow are you\b"}
        zh_direct_keywords = {"解释", "什么是", "为什么", "你好", "继续"}
        has_direct = any(re.search(kw, lowered) for kw in direct_keywords) or any(kw in lowered for kw in zh_direct_keywords)
        if has_direct and not self._has_write_keywords(lowered):
            return ExecutionMode.DIRECT

        # 3. Research-only
        research_keywords = {"analyze", "find", "search", "read", "lookup", "explain", "where", "list"}
        zh_research_keywords = {"分析", "查找", "搜索", "读取", "定位", "列表"}
        has_research = any(kw in lowered for kw in research_keywords) or any(kw in lowered for kw in zh_research_keywords)
        if has_research and not self._has_write_keywords(lowered):
            return ExecutionMode.RESEARCH_ONLY

        # 4. Multi-file or complex DAG execution
        dag_keywords = {"refactor", "across modules", "multi-file", "all files", "whole project", "integrate"}
        zh_dag_keywords = {"重构", "跨模块", "多文件", "所有文件", "整个项目", "集成"}
        if any(kw in lowered for kw in dag_keywords) or any(kw in lowered for kw in zh_dag_keywords):
            return ExecutionMode.DAG_EXECUTE

        # 5. Default fallback to linear-edit or research-only
        if self._has_write_keywords(lowered):
            return ExecutionMode.LINEAR_EDIT

        return ExecutionMode.RESEARCH_ONLY

    def _has_write_keywords(self, text: str) -> bool:
        write_keywords = {r"\bfix\b", r"\bpatch\b", r"\bmodify\b", r"\bdelete\b", r"\bwrite\b", r"\bupdate\b", r"\bimplement\b", r"\bcreate\b"}
        zh_write_keywords = {"修复", "补丁", "修改", "删除", "写入", "更新", "实现", "创建", "继续推进"}
        return any(re.search(kw, text) for kw in write_keywords) or any(kw in text for kw in zh_write_keywords)

