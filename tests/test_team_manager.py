"""TeamManager 方法测试：覆盖 TUI 后台轮询/通知会用到的接口。"""
from __future__ import annotations

from xhx_agent.teams.manager import TeamManager


def test_drain_lead_mailbox_without_team_name_aggregates() -> None:
    # TUI 全局轮询无参调用：无团队时应返回空列表而非抛 TypeError。
    mgr = TeamManager()

    assert mgr.drain_lead_mailbox() == []


def test_on_teammate_completed_unknown_agent_is_safe() -> None:
    # 后台任务完成回调：未知 agent_id 应安全跳过，不抛异常。
    mgr = TeamManager()

    mgr.on_teammate_completed("nonexistent-agent")  # 不应抛错


def test_drain_lead_mailbox_specific_team_still_works() -> None:
    # 既有按团队名调用的语义保持不变。
    mgr = TeamManager()

    assert mgr.drain_lead_mailbox("no-such-team") == []
