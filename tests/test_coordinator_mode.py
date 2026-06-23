"""coordinator 协调者模式激活回归。

此前 enable_coordinator_mode 是死开关：缺 apply_coordinator_filter/is_coordinator_mode，
TeamCreate 也不消费它。本测试验证开启后 TeamCreate 真把顶层 agent 的工具收窄为
仅派发/通信，并保留完整 registry 到 _full_registry。
"""

from __future__ import annotations

import asyncio


class _FakeAgent:
    agent_id = "lead-x"
    coordinator_mode = False


def test_coordinator_mode_activates_and_filters(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("XHX_COORDINATOR_MODE", raising=False)
    from xhx_agent.teams.manager import TeamManager
    from xhx_agent.tools import create_default_registry
    from xhx_agent.tools.synthetic_output import SyntheticOutputTool
    from xhx_agent.tools.team_create import TeamCreateParams, TeamCreateTool

    reg = create_default_registry()
    reg.register(SyntheticOutputTool())  # 白名单内工具
    orig = {t.name for t in reg.list_tools()}
    assert "Bash" in orig and "SyntheticOutput" in orig

    agent = _FakeAgent()
    agent.registry = reg  # type: ignore[attr-defined]

    tc = TeamCreateTool(team_manager=TeamManager(), parent_agent=agent, enable_coordinator_mode=True)

    async def run() -> None:
        r = await tc.execute(TeamCreateParams(name="co"))
        assert not r.is_error, r.output
        assert "协调者模式已激活" in r.output
        assert agent.coordinator_mode is True
        names = {t.name for t in agent.registry.list_tools()}  # type: ignore[attr-defined]
        assert "SyntheticOutput" in names  # 白名单工具保留
        assert "Bash" not in names  # 非白名单被滤掉
        assert agent._full_registry is reg  # type: ignore[attr-defined]  # 完整 registry 保留

    asyncio.run(run())


def test_coordinator_mode_off_keeps_full_registry(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.teams.manager import TeamManager
    from xhx_agent.tools import create_default_registry
    from xhx_agent.tools.team_create import TeamCreateParams, TeamCreateTool

    reg = create_default_registry()
    n = len(reg.list_tools())
    agent = _FakeAgent()
    agent.registry = reg  # type: ignore[attr-defined]

    tc = TeamCreateTool(team_manager=TeamManager(), parent_agent=agent, enable_coordinator_mode=False)

    async def run() -> None:
        r = await tc.execute(TeamCreateParams(name="co2"))
        assert not r.is_error, r.output
        assert agent.coordinator_mode is False
        assert len(agent.registry.list_tools()) == n  # type: ignore[attr-defined]  # registry 不变

    asyncio.run(run())


def test_is_coordinator_mode_env_override(monkeypatch) -> None:
    from xhx_agent.teams.coordinator import is_coordinator_mode

    monkeypatch.delenv("XHX_COORDINATOR_MODE", raising=False)
    assert is_coordinator_mode(True) is True
    assert is_coordinator_mode(False) is False
    monkeypatch.setenv("XHX_COORDINATOR_MODE", "off")
    assert is_coordinator_mode(True) is False  # 环境变量可强制关闭
