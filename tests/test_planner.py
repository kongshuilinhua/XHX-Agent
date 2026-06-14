from __future__ import annotations

from pathlib import Path

import pytest

from xhx_agent.planner.classifier import ModeClassifier
from xhx_agent.planner.modes import DAGNode, DAGPlan, ExecutionMode
from xhx_agent.planner.planner import DAGScheduler, topological_sort
from xhx_agent.planner.reviewer import Reviewer
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.terminal import TerminalResult


def test_mode_classifier_categories() -> None:
    classifier = ModeClassifier()

    # 1. Direct
    assert classifier.classify("exit") == ExecutionMode.DIRECT
    assert classifier.classify("help") == ExecutionMode.DIRECT
    assert classifier.classify("what is Python?") == ExecutionMode.DIRECT

    # 2. Research-only
    assert classifier.classify("analyze this repo") == ExecutionMode.RESEARCH_ONLY
    assert classifier.classify("search for add function") == ExecutionMode.RESEARCH_ONLY

    # 3. Multi-file/refactor edits → linear-edit（dag 模式已退役）
    assert classifier.classify("refactor math functions") == ExecutionMode.LINEAR_EDIT
    assert classifier.classify("integrate all files") == ExecutionMode.LINEAR_EDIT

    # 4. Linear-edit
    assert classifier.classify("fix calc.py buggy code") == ExecutionMode.LINEAR_EDIT
    assert classifier.classify("update README file") == ExecutionMode.LINEAR_EDIT


def test_topological_sort_and_cycle_detection() -> None:
    # 1. Straightforward DAG
    nodes = [
        DAGNode(node_id="A", description="A", tool="read_file", dependencies=[]),
        DAGNode(node_id="B", description="B", tool="read_file", dependencies=["A"]),
        DAGNode(node_id="C", description="C", tool="read_file", dependencies=["A", "B"]),
    ]
    ordered = topological_sort(nodes)
    assert [n.node_id for n in ordered] == ["A", "B", "C"]

    # 2. Cycle detection
    cyclic_nodes = [
        DAGNode(node_id="A", description="A", tool="read_file", dependencies=["B"]),
        DAGNode(node_id="B", description="B", tool="read_file", dependencies=["A"]),
    ]
    with pytest.raises(ValueError, match="Cycle detected"):
        topological_sort(cyclic_nodes)


def test_dag_scheduler_success_and_blocked() -> None:
    nodes = [
        DAGNode(node_id="A", description="A", tool="read_file", dependencies=[]),
        DAGNode(node_id="B", description="B", tool="read_file", dependencies=["A"]),
        DAGNode(node_id="C", description="C", tool="read_file", dependencies=["B"]),
    ]
    plan = DAGPlan(root="demo", nodes=nodes)

    # Execution records
    executed = []

    def fake_execute(node):
        executed.append(node.node_id)
        if node.node_id == "B":
            # Simulate a node failure!
            return False, "Node B failed"
        return True, "Success"

    scheduler = DAGScheduler(Path("demo"))
    success = scheduler.execute(plan, fake_execute)

    # Node B failed, so C should be blocked and not executed!
    assert success is False
    assert executed == ["A", "B"]
    assert plan.nodes[0].status == "success"
    assert plan.nodes[1].status == "failed"
    assert plan.nodes[2].status == "blocked"


def test_reviewer_quality_gate() -> None:
    reviewer = Reviewer()

    # 1. Changed files but no tests -> Fail
    decision1 = reviewer.review("fix calc", ["src/calc.py"], [])
    assert decision1.passed is False
    assert "not verified" in decision1.reason

    # 2. Any test fails -> Fail
    failed_test = TerminalResult(
        command="python -m pytest",
        status="failed",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="allow"),
        exit_code=1,
        summary="failed",
    )
    decision2 = reviewer.review("fix calc", ["src/calc.py"], [failed_test])
    assert decision2.passed is False
    assert "failed" in decision2.reason

    # 3. All tests pass -> Pass
    passed_test = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="allow"),
        exit_code=0,
        summary="passed",
    )
    decision3 = reviewer.review("fix calc", ["src/calc.py"], [passed_test])
    assert decision3.passed is True
    assert "successfully" in decision3.reason


def test_runtime_app_routes_by_mode(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()

    # 1. Direct Q&A mode
    res_direct = app.run_task("what is Python?", mode="linear")
    assert res_direct.status == "success"
    assert res_direct.verification == "skipped_no_changes"

    # 2. Research-only mode
    res_research = app.run_task("analyze this repo", mode="linear")
    assert res_research.status == "success"
    assert res_research.verification == "skipped_no_changes"


def test_dagnode_has_agent_fields() -> None:
    from xhx_agent.planner.modes import DAGNode
    n = DAGNode(node_id="n1", description="d", agent_type="edit", prompt="do x", dependencies=[])
    assert n.agent_type == "edit"
    assert n.prompt == "do x"
    # 默认值（向后兼容现有调用）
    d = DAGNode(node_id="n2", description="d")
    assert d.agent_type == "explore" and d.prompt == ""

