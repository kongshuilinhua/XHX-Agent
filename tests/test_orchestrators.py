from pathlib import Path

from xhx_agent.orchestrators.base import Orchestrator, OrchestratorContext


def test_orchestrator_context_is_constructible_with_minimal_fields() -> None:
    ctx = OrchestratorContext(
        app=None,
        task="t",
        run_id="r",
        workspace=Path("."),
        profile=None,
        scan=None,
        evidence=None,
        kernel=None,
        tool_context=None,
    )
    assert ctx.assume_yes is False
    assert ctx.auto_repair is False
    assert ctx.metrics_tracker == {"tokens": 0}


def test_orchestrator_protocol_has_run_member() -> None:
    assert hasattr(Orchestrator, "run")


def test_dag_orchestrator_delegates_to_dag_runner(monkeypatch) -> None:
    from xhx_agent.orchestrators.dag import DagOrchestrator

    captured: dict = {}

    def fake_run_dag(self, **kwargs):
        captured.update(kwargs)
        return "RESULT"

    monkeypatch.setattr("xhx_agent.runtime.dag_runner.DAGRunner.run_dag", fake_run_dag)

    class _StubApp:
        workspace = Path(".")

    ctx = OrchestratorContext(
        app=_StubApp(),
        task="do x",
        run_id="r1",
        workspace=Path("."),
        profile=None,
        scan=None,
        evidence=None,
        kernel=None,
        tool_context=None,
    )
    orch = DagOrchestrator()
    assert orch.name == "dag"
    assert orch.run(ctx) == "RESULT"
    assert captured["task"] == "do x"
    assert captured["run_id"] == "r1"
    assert captured["start_time"] == ctx.start_time
