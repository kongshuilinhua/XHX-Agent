from xhx_agent.orchestrators.registry import select_orchestrator


def test_plan_mode_selects_plan_orchestrator():
    assert select_orchestrator("plan").name == "plan"


def test_loop_alias_still_plan_for_now():
    # rename phase: loop is temporarily an alias to plan (new loop takes over in a later task)
    assert select_orchestrator("loop").name == "plan"
