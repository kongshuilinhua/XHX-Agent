from __future__ import annotations

from pathlib import Path

from xhx_agent.planner.agents import CoderAgent, PlannerAgent, ReviewerAgent
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.terminal import TerminalResult


def test_agent_roles_initialization(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_calc.py").write_text("from calc import add\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()

    planner = PlannerAgent(app)
    coder = CoderAgent(app)
    reviewer = ReviewerAgent()

    assert planner.app == app
    assert coder.app == app
    assert isinstance(reviewer, ReviewerAgent)


def test_reviewer_agent_quality_gate() -> None:
    reviewer = ReviewerAgent()

    # Case 1: Unverified changed files
    decision1 = reviewer.review("refactor", ["src/calc.py"], [])
    assert decision1.passed is False
    assert "not verified" in decision1.reason

    # Case 2: Failed verification results
    failed_test = TerminalResult(
        command="python -m pytest",
        status="failed",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="allow"),
        exit_code=1,
        summary="failed",
    )
    decision2 = reviewer.review("refactor", ["src/calc.py"], [failed_test])
    assert decision2.passed is False
    assert "failed" in decision2.reason

    # Case 3: Passed verification results
    passed_test = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="allow"),
        exit_code=0,
        summary="passed",
    )
    decision3 = reviewer.review("refactor", ["src/calc.py"], [passed_test])
    assert decision3.passed is True
    assert "successfully" in decision3.reason
