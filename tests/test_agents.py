"""Agent 相关测试：不再走旧 RuntimeApp/PlannerAgent/CoderAgent 路径。

旧栈 planner 已删除；新栈 Agent 的测试见 test_headless.py。
"""

from __future__ import annotations


def test_new_agent_imports() -> None:
    """验证新栈 Agent 可正常导入。"""
    from xhx_agent.agents.agent_runner import Agent  # noqa: F401


def test_headless_result_imports() -> None:
    """验证 HeadlessResult 可正常导入。"""
    from xhx_agent.runtime.headless import HeadlessResult  # noqa: F401
