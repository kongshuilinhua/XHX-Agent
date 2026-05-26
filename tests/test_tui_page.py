from rich.console import Console

from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.page import render_console_page
from xhx_agent.tui.state import ConsoleState


def test_render_console_page_shows_runtime_sections() -> None:
    state = ConsoleState()
    state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix tests", "profile": "mock"}))
    state.reduce(RuntimeEvent(type="context_pack", message="Context pack compiled.", payload={"turn": 1, "selected": 2, "omitted": 1, "used_tokens_estimate": 100, "budget_tokens": 6000}))
    state.reduce(RuntimeEvent(type="model_plan", message="Patch calculation", payload={"status": "continue", "step_count": 1}))
    state.reduce(RuntimeEvent(type="tool_start", message="Tool started.", payload={"turn": 1, "tool": "apply_patch"}))
    state.reduce(RuntimeEvent(type="tool_result", message="Tool finished.", payload={"turn": 1, "tool": "apply_patch", "status": "success", "summary": "changed files: src/calc.py"}))
    state.reduce(RuntimeEvent(type="run_end", message="Run finished.", payload={"status": "success", "verification": "passed", "changed_files": ["src/calc.py"], "summary_path": ".xhx/logbook/run-1.md"}))

    console = Console(record=True, force_terminal=False, width=120)
    console.print(
        render_console_page(
            state,
            workspace="D:/repo",
            profile="mock",
            auto_repair=False,
            assume_yes=True,
        )
    )
    output = console.export_text()

    assert "xhx-agent" in output
    assert "Conversation" in output
    assert "Runtime State" in output
    assert "Context" in output
    assert "Changed Files" in output
    assert "src/calc.py" in output
    assert "/dashboard" in output
    assert "/cancel" in output


def test_render_console_page_shows_cancel_state() -> None:
    state = ConsoleState()
    state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix tests", "profile": "mock"}))
    state.reduce(RuntimeEvent(type="cancel_requested", message="Cancel requested by user.", payload={"source": "console"}))

    console = Console(record=True, force_terminal=False, width=120)
    console.print(
        render_console_page(
            state,
            workspace="D:/repo",
            profile="mock",
            auto_repair=False,
            assume_yes=False,
        )
    )
    output = console.export_text()

    assert "cancel: yes" in output
    assert "Cancel requested by user." in output
