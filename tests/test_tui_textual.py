from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.state import ConsoleState
from xhx_agent.tui.textual_app import TextualCommandConsoleApp, TextualSnapshot
from xhx_agent.runtime.app import ManualVerificationResult, RunResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel


def test_textual_snapshot_from_console_state_shows_status_and_commands() -> None:
    state = ConsoleState()
    state.reduce(
        RuntimeEvent(
            type="run_start",
            message="Run started.",
            payload={"run_id": "run-1", "task": "fix tests", "profile": "mock"},
        )
    )
    state.reduce(
        RuntimeEvent(
            type="context_pack",
            message="Context pack compiled.",
            payload={"turn": 1, "selected": 3, "omitted": 1, "used_tokens_estimate": 120, "budget_tokens": 6000},
        )
    )
    state.reduce(RuntimeEvent(type="model_plan", message="Patch failing test", payload={"status": "continue", "step_count": 1}))
    state.reduce(
        RuntimeEvent(
            type="run_end",
            message="Run finished.",
            payload={"status": "success", "verification": "passed", "changed_files": ["src/calc.py"], "summary_path": ".xhx/logbook/run-1.md"},
        )
    )

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="D:/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )

    assert snapshot.header == "xhx-agent | success | profile: mock | run: run-1"
    assert "fix tests" in snapshot.conversation
    assert "Patch failing test" in snapshot.conversation
    assert "verification: passed" in snapshot.runtime_state
    assert "context: 120/6000" in snapshot.runtime_state
    assert "src/calc.py" in snapshot.changed_files
    assert "/help /model /status" in snapshot.commands


def test_textual_command_console_app_can_render_initial_shell(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            assert app.title.startswith("xhx-agent | idle | profile: mock")
            assert "No conversation yet." in str(pilot.app.query_one("#conversation").content)
            assert "changed files:" in str(pilot.app.query_one("#changed").content)

    import asyncio

    asyncio.run(run_app())


def test_textual_command_console_handles_read_only_slash_commands(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/help")
    assert "available commands" in app.messages[-1]

    assert app.handle_text_input("/status")
    assert "verification: not_started" in app.messages[-1]

    assert app.handle_text_input("/unknown")
    assert "Unknown command: /unknown" in app.messages[-1]


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []
        self.verify_calls = []

    def run_task(self, task, **kwargs):
        self.calls.append((task, kwargs))
        kwargs["event_callback"](RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": task, "profile": "mock"}))
        kwargs["event_callback"](RuntimeEvent(type="model_plan", message="Analyze task", payload={"status": "done", "step_count": 0}))
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_end",
                message="Run finished.",
                payload={"run_id": "run-1", "status": "success", "verification": "skipped_no_changes", "changed_files": [], "summary_path": ".xhx/logbook/run-1.md"},
            )
        )
        return RunResult(
            run_id="run-1",
            status="success",
            changed_files=[],
            commands=[],
            verification="skipped_no_changes",
            summary_path=".xhx/logbook/run-1.md",
            risk_summary=[],
        )

    def verify_changed_files(self, changed_files, **kwargs):
        self.verify_calls.append((changed_files, kwargs))
        kwargs["event_callback"](RuntimeEvent(type="run_start", message="Manual verification started.", payload={"run_id": "verify-1", "task": "manual verification", "profile": "manual"}))
        kwargs["event_callback"](RuntimeEvent(type="verification_start", message="Verification started.", payload={"command": "python -m pytest"}))
        allowed = kwargs["confirm_callback"](
            "python -m pytest",
            PolicyDecision(decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True),
        )
        status = "success" if allowed else "confirm"
        kwargs["event_callback"](RuntimeEvent(type="verification_result", message="Verification finished.", payload={"command": "python -m pytest", "status": status, "exit_code": 0 if allowed else None}))
        return ManualVerificationResult(
            run_id="verify-1",
            status="passed" if allowed else "requires_confirmation",
            changed_files=list(changed_files),
            commands=["python -m pytest"],
            summary_path=".xhx/logbook/verify-1.md",
            risk_summary=[] if allowed else ["Verification requires confirmation."],
        )


def test_textual_command_console_runs_task_through_runtime(tmp_path) -> None:
    runtime = FakeRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    assert app.handle_text_input("fix tests")

    assert runtime.calls
    task, kwargs = runtime.calls[0]
    assert task == "fix tests"
    assert kwargs["profile_name"] == "mock"
    assert kwargs["auto_repair"] is False
    assert app.last_result is not None
    assert app.last_result.status == "success"
    assert app.state.status == "success"
    assert app.state.task == "fix tests"
    assert app.state.summary_path == ".xhx/logbook/run-1.md"
    assert "Analyze task" in app.state.plan_summary


def test_textual_command_console_clear_and_exit_are_local(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    app.messages.append("system> old")
    app.state.reduce(RuntimeEvent(type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix", "profile": "mock"}))

    assert app.handle_text_input("/clear")
    assert app.messages == []
    assert app.state.status == "idle"

    assert app.handle_text_input("/exit") is False
    assert app.exit_requested is True


def test_textual_command_console_submitted_input_updates_window(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "h", "e", "l", "p", "enter")
            assert "available commands" in str(pilot.app.query_one("#conversation").content)

    import asyncio

    asyncio.run(run_app())


def test_textual_command_console_submitted_task_updates_window(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("f", "i", "x", "enter")
            assert "summary> .xhx/logbook/run-1.md" in str(pilot.app.query_one("#conversation").content)
            assert "verification: skipped_no_changes" in str(pilot.app.query_one("#runtime").content)

    import asyncio

    asyncio.run(run_app())


def test_textual_command_console_verify_uses_current_changed_files(tmp_path) -> None:
    runtime = FakeRuntime()
    state = ConsoleState()
    state.changed_files = ["src/calc.py"]
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime, state=state)
    app.next_confirm_response = True

    assert app.handle_text_input("/verify")

    assert runtime.verify_calls
    changed_files, kwargs = runtime.verify_calls[0]
    assert changed_files == ["src/calc.py"]
    assert kwargs["assume_yes"] is False
    assert app.last_manual_verification is not None
    assert app.last_manual_verification.status == "passed"
    assert "manual verification: passed" in app.messages[-1]
    assert app.next_confirm_response is None


def test_textual_permission_confirmation_can_decline_once(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    app.next_confirm_response = False

    allowed = app.confirm_terminal_command(
        "python -m pytest",
        PolicyDecision(decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True),
    )

    assert allowed is False
    assert app.next_confirm_response is None
    assert "declined" in app.messages[-1]


def test_textual_permission_confirmation_can_allow_once(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    app.next_confirm_response = True

    allowed = app.confirm_terminal_command(
        "python -m pytest",
        PolicyDecision(decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True),
    )

    assert allowed is True
    assert app.next_confirm_response is None
    assert "allowed" in app.messages[-1]
