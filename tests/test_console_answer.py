import io

from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.runtime.app import RunResult


def test_print_run_result_shows_answer(tmp_path):
    buf = io.StringIO()
    console = CommandConsole(tmp_path, console=Console(file=buf, width=100))
    res = RunResult(run_id="r1", status="success", changed_files=[], commands=[],
                    verification="not_executed", summary_path="p", risk_summary=[], mode="loop",
                    answer="这是 loop 的回答")
    console.print_run_result(res)
    assert "这是 loop 的回答" in buf.getvalue()


def test_console_loop_and_plan_are_orchestrator_modes(tmp_path):
    cc = CommandConsole(tmp_path, console=Console(file=io.StringIO()))
    cc.set_mode("plan")
    assert cc.orchestrator_mode == "plan"
    cc.set_mode("loop")
    assert cc.orchestrator_mode == "loop"
