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
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    class _Fake:
        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return ChatResult(content="I'll analyze this repo with my team.")
            return ChatResult(content="Analysis complete.")

    monkeypatch.setattr("xhx_agent.orchestrators.team.build_chat_client", lambda profile: _Fake())
    # Also stub the summarizer client
    monkeypatch.setattr("xhx_agent.orchestrators.team.build_routed_client",
                        lambda *a, **kw: _Fake())

    events = []
    result = RuntimeApp(tmp_path).run_task("analyze this repo", assume_yes=True, mode="team",
                                            event_callback=events.append)
    assert result.mode == "team"
    assert result.status == "success"
