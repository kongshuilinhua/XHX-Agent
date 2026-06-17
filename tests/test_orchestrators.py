from pathlib import Path

import pytest

from xhx_agent.orchestrators.base import Orchestrator, OrchestratorContext


def test_orchestrator_context_is_constructible_with_minimal_fields() -> None:
    ctx = OrchestratorContext(
        app=None, task="t", run_id="r",
        workspace=Path("."), original_workspace=Path("."),
        profile=None, scan=None, evidence=None, kernel=None, tool_context=None,
    )
    assert ctx.assume_yes is False
    assert ctx.auto_repair is False
    assert ctx.autonomous is False
    assert ctx.metrics_tracker == {"tokens": 0}


def test_orchestrator_protocol_has_run_member() -> None:
    assert hasattr(Orchestrator, "run")


def test_select_orchestrator_defaults_and_errors() -> None:
    from xhx_agent.orchestrators.registry import select_orchestrator

    assert select_orchestrator(None).name == "loop"
    assert select_orchestrator("loop").name == "loop"
    assert select_orchestrator("plan").name == "plan"
    assert select_orchestrator("team").name == "team"
    with pytest.raises(ValueError):
        select_orchestrator("nope")


def test_team_mode_runs_via_coordinator(tmp_path, monkeypatch) -> None:
    """Verify TeamOrchestrator is registered and instantiable."""
    from xhx_agent.orchestrators.team import TeamOrchestrator
    o = TeamOrchestrator()
    assert o.name == "team"

    from xhx_agent.orchestrators.registry import select_orchestrator
    team_o = select_orchestrator("team")
    assert team_o.name == "team"

    from xhx_agent.teams.coordinator import get_coordinator_system_prompt
    prompt = get_coordinator_system_prompt([("Explore", "search agent"), ("general-purpose", "full agent")])
    assert "Explore" in prompt
    assert "general-purpose" in prompt
