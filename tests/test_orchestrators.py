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

    # DEFAULT_MODE="loop" -> LoopOrchestrator (named "loop"); "plan" -> PlanOrchestrator
    assert select_orchestrator(None).name == "loop"
    assert select_orchestrator("loop").name == "loop"
    assert select_orchestrator("plan").name == "plan"
    assert select_orchestrator("linear").name == "linear"
    assert select_orchestrator("graph").name == "graph"
    assert select_orchestrator("dag").name == "dag"
    assert execution_mode_to_key(ExecutionMode.DAG_EXECUTE) == "dag"
    assert execution_mode_to_key(ExecutionMode.LINEAR_EDIT) == "linear"
    with pytest.raises(ValueError):
        select_orchestrator("nope")


def test_graph_mode_runs_via_langgraph(tmp_path, monkeypatch) -> None:
    import xhx_agent.orchestrators.graph as graphmod
    from xhx_agent.models.types import ChatResult, ToolCall
    from xhx_agent.runtime.app import RuntimeApp

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    # tool-calling graph：coordinator(LLM 拆子任务) -> worker(apply_patch 真改) -> reviewer(PASS)。
    class _Fake:
        def __init__(self) -> None:
            self.w = 0

        def chat(self, messages, tools):
            system = messages[0]["content"]
            if "COORDINATOR" in system:
                return ChatResult(content="- tweak calc.py")
            if "REVIEWER" in system:
                return ChatResult(content="PASS")
            self.w += 1
            if self.w == 1:
                return ChatResult(content=None, tool_calls=[ToolCall(id="w1", name="apply_patch", arguments={
                    "patch": "*** Begin Patch\n*** Update File: src/calc.py\n@@\n"
                             "-    return a + b\n+    return a + b  # tweaked\n*** End Patch\n"})])
            return ChatResult(content="done")

    monkeypatch.setattr(graphmod, "build_chat_client", lambda profile: _Fake())
    events = []

    result = RuntimeApp(tmp_path).run_task("refactor math", assume_yes=True, mode="graph", event_callback=events.append)

    assert result.mode == "graph"
    assert any(e.type == "graph_coordinator" for e in events)
    assert any(e.type == "graph_execute" for e in events)
    assert any(e.type == "graph_review" for e in events)
    assert result.changed_files == ["src/calc.py"]
    assert result.status == "success"


def test_console_orchestrator_mode_from_label(tmp_path) -> None:
    from rich.console import Console

    from xhx_agent.cli.console import CommandConsole

    cc = CommandConsole(tmp_path, console=Console())
    # default label ("linear-edit") is not an orchestrator key -> auto-classify
    assert cc.orchestrator_mode is None
    cc.set_mode("loop")
    assert cc.orchestrator_mode == "loop"
    cc.set_mode("graph")
    assert cc.orchestrator_mode == "graph"
    cc.set_mode("research-only")
    assert cc.orchestrator_mode is None
