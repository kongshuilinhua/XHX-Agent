from xhx_agent.orchestrators.registry import select_orchestrator


def test_plan_mode_selects_plan_orchestrator():
    assert select_orchestrator("plan").name == "plan"


def test_loop_mode_selects_loop_orchestrator():
    assert select_orchestrator("loop").name == "loop"
