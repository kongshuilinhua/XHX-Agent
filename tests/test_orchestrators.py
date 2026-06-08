from pathlib import Path

import pytest

from xhx_agent.orchestrators.base import Orchestrator, OrchestratorContext


def test_orchestrator_context_is_constructible_with_minimal_fields() -> None:
    ctx = OrchestratorContext(
        app=None,
        task="t",
        run_id="r",
        workspace=Path("."),
        original_workspace=Path("."),
        profile=None,
        scan=None,
        evidence=None,
        kernel=None,
        tool_context=None,
    )
    assert ctx.assume_yes is False
    assert ctx.auto_repair is False
    assert ctx.autonomous is False
    assert ctx.metrics_tracker == {"tokens": 0}


def test_orchestrator_protocol_has_run_member() -> None:
    assert hasattr(Orchestrator, "run")


def test_dag_orchestrator_delegates_to_dag_runner(monkeypatch) -> None:
    from xhx_agent.orchestrators.dag import DagOrchestrator

    captured: dict = {}

    class _Result:
        def __init__(self) -> None:
            self.mode = ""
            self.status = "success"
            self.changed_files: list = []
            self.risk_summary: list = []

    def fake_run_dag(self, **kwargs):
        captured.update(kwargs)
        return _Result()

    monkeypatch.setattr("xhx_agent.runtime.dag_runner.DAGRunner.run_dag", fake_run_dag)

    class _StubApp:
        workspace = Path(".")

    ctx = OrchestratorContext(
        app=_StubApp(),
        task="do x",
        run_id="r1",
        workspace=Path("."),
        original_workspace=Path("."),
        profile=None,
        scan=None,
        evidence=None,
        kernel=None,
        tool_context=None,
    )
    orch = DagOrchestrator()
    assert orch.name == "dag"
    result = orch.run(ctx)
    assert result.mode == ctx.mode
    assert captured["task"] == "do x"
    assert captured["run_id"] == "r1"
    assert captured["start_time"] == ctx.start_time


def test_select_orchestrator_defaults_and_errors() -> None:
    from xhx_agent.orchestrators.registry import execution_mode_to_key, select_orchestrator
    from xhx_agent.planner.modes import ExecutionMode

    assert select_orchestrator(None).name == "linear"
    assert select_orchestrator("loop").name == "linear"
    assert select_orchestrator("graph").name == "dag"
    assert select_orchestrator("dag").name == "dag"
    assert execution_mode_to_key(ExecutionMode.DAG_EXECUTE) == "dag"
    assert execution_mode_to_key(ExecutionMode.LINEAR_EDIT) == "linear"
    with pytest.raises(ValueError):
        select_orchestrator("nope")
