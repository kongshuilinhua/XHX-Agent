from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.state import ConsoleState


def test_console_state_reduces_runtime_events() -> None:
    state = ConsoleState()

    state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix", "profile": "mock"}))
    state.reduce(RuntimeEvent(type="scan", message="Scan.", payload={"detected_languages": ["python"], "file_count": 4}))
    state.reduce(
        RuntimeEvent(
            type="context_pack",
            message="Context.",
            payload={"turn": 1, "selected": 3, "omitted": 1, "used_tokens_estimate": 120, "budget_tokens": 6000},
        )
    )
    state.reduce(RuntimeEvent(type="model_plan", message="Patch calc", payload={"turn": 1, "step_count": 1, "status": "continue"}))
    state.reduce(RuntimeEvent(type="tool_start", message="Tool started.", payload={"turn": 1, "tool": "apply_patch"}))
    state.reduce(
        RuntimeEvent(
            type="tool_result",
            message="Tool finished.",
            payload={"turn": 1, "tool": "apply_patch", "status": "success", "summary": "changed files: src/calc.py"},
        )
    )
    state.reduce(
        RuntimeEvent(
            type="policy_decision",
            message="Command requires user confirmation.",
            payload={
                "scope": "terminal",
                "source": "uv run pytest",
                "decision": "confirm",
                "risk": "confirm",
                "reason": "Command requires user confirmation.",
                "requires_user": True,
            },
        )
    )
    state.reduce(RuntimeEvent(type="verification_start", message="Verify.", payload={"command": "uv run pytest"}))
    state.reduce(
        RuntimeEvent(
            type="verification_result",
            message="Verified.",
            payload={"command": "uv run pytest", "status": "success", "exit_code": 0},
        )
    )
    state.reduce(
        RuntimeEvent(
            type="run_end",
            message="Done.",
            payload={
                "run_id": "run-1",
                "status": "success",
                "verification": "passed",
                "changed_files": ["src/calc.py"],
                "summary_path": ".xhx/logbook/run-1.md",
            },
        )
    )

    assert state.status == "success"
    assert state.run_id == "run-1"
    assert state.detected_languages == ["python"]
    assert state.context_selected == 3
    assert state.plan_summary == "Patch calc"
    state.reduce(RuntimeEvent(type="model_delta", message='{"summary":"Patch', payload={"turn": 1, "length": 18}))
    state.reduce(RuntimeEvent(type="model_delta", message=' calc"}', payload={"turn": 1, "length": 7}))
    assert state.model_output == '{"summary":"Patch calc"}'
    assert state.model_delta_count == 2
    assert state.tools[0].status == "success"
    assert state.policy_decisions[0].requires_user
    assert state.verification == "passed"
    assert state.verifications[0].exit_code == 0
    assert state.changed_files == ["src/calc.py"]
    assert state.summary_path == ".xhx/logbook/run-1.md"


def test_console_state_reduces_cancel_events() -> None:
    state = ConsoleState()

    state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix", "profile": "mock"}))
    state.reduce(RuntimeEvent(type="cancel_requested", message="Cancel requested by user.", payload={"source": "console"}))
    state.reduce(RuntimeEvent(type="run_cancelled", message="Run cancelled before verification.", payload={"run_id": "run-1"}))

    assert state.status == "cancelled"
    assert state.cancel_requested is True
    assert state.cancel_reason == "Run cancelled before verification."
    assert state.verification == "cancelled"
