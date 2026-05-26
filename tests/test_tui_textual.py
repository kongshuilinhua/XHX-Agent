from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.tui.state import ConsoleState
from xhx_agent.tui.textual_app import TextualCommandConsoleApp, TextualSnapshot
from xhx_agent.runtime.app import DiffSummary, ManualRepairResult, ManualVerificationResult, PlanPreview, RunResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.terminal import TerminalResult


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
        self.diff_calls = []
        self.plan_calls = []
        self.repair_calls = []

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

    def diff_changed_files(self, changed_files):
        self.diff_calls.append(list(changed_files))
        return DiffSummary(
            changed_files=list(changed_files),
            summary=f"{len(changed_files)} changed file(s).",
            diff_text="diff --git a/src/calc.py b/src/calc.py\n+return a + b\n",
            truncated=False,
            risk_summary=[],
        )

    def preview_plan(self, task, profile_name=None):
        self.plan_calls.append((task, profile_name))
        return PlanPreview(
            run_id="dry-run-1",
            status="success",
            summary=f"Preview {task}",
            step_count=2,
            context_budget_tokens=6000,
            context_used_tokens_estimate=120,
            trace_path=".xhx/traces/dry-run-1.jsonl",
            risk_summary=[],
        )

    def repair_after_failed_verification(self, **kwargs):
        self.repair_calls.append(kwargs)
        kwargs["event_callback"](RuntimeEvent(type="run_start", message="Manual repair started.", payload={"run_id": "repair-1", "task": kwargs["task"], "profile": "mock"}))
        kwargs["event_callback"](RuntimeEvent(type="repair_start", message="Manual repair attempt started.", payload={"attempt": 1, "max_attempts": kwargs["max_attempts"]}))
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_end",
                message="Manual repair finished.",
                payload={"run_id": "repair-1", "status": "success", "verification": "passed", "changed_files": kwargs["changed_files"], "summary_path": ".xhx/logbook/repair-1.md"},
            )
        )
        return ManualRepairResult(
            run_id="repair-1",
            status="success",
            changed_files=list(kwargs["changed_files"]),
            commands=["python -m pytest"],
            verification="passed",
            verification_results=[],
            repair_attempts=1,
            summary_path=".xhx/logbook/repair-1.md",
            risk_summary=[],
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


def test_textual_context_command_summarizes_current_state(tmp_path) -> None:
    state = ConsoleState()
    state.context_turn = 2
    state.context_selected = 5
    state.context_omitted = 1
    state.context_used_tokens_estimate = 400
    state.context_budget_tokens = 6000
    state.detected_languages = ["python"]
    state.file_count = 9
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", state=state)

    assert app.handle_text_input("/context")

    assert "context: turn=2 selected=5 omitted=1 budget=400/6000 languages=python files=9" in app.messages[-1]


def test_textual_evidence_command_summarizes_policy_decisions(tmp_path) -> None:
    state = ConsoleState()
    state.reduce(
        RuntimeEvent(
            type="policy_decision",
            message="Command requires confirmation.",
            payload={
                "scope": "terminal",
                "source": "python -m pytest",
                "decision": "confirm",
                "risk": "confirm",
                "reason": "Needs confirmation.",
                "requires_user": True,
            },
        )
    )
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", state=state)

    assert app.handle_text_input("/evidence")

    assert "policy evidence" in app.messages[-1]
    assert "python -m pytest" in app.messages[-1]


def test_textual_diff_command_uses_runtime_read_only_summary(tmp_path) -> None:
    runtime = FakeRuntime()
    state = ConsoleState()
    state.changed_files = ["src/calc.py"]
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime, state=state)

    assert app.handle_text_input("/diff")

    assert runtime.diff_calls == [["src/calc.py"]]
    assert "1 changed file(s)." in app.messages[-1]
    assert "+return a + b" in app.messages[-1]


def test_textual_plan_command_previews_task_through_runtime(tmp_path) -> None:
    runtime = FakeRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    assert app.handle_text_input("/plan analyze repo")

    assert runtime.plan_calls == [("analyze repo", "mock")]
    assert "plan preview: success" in app.messages[-1]
    assert "Preview analyze repo" in app.messages[-1]
    assert "steps=2" in app.messages[-1]


def test_textual_mode_command_shows_and_updates_state_mode(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/mode")
    assert "mode: linear-edit" in app.messages[-1]

    assert app.handle_text_input("/mode research-only")

    assert app.state.mode == "research-only"
    assert "mode: research-only" in app.messages[-1]


def test_textual_repair_command_requires_failed_verification(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())

    assert app.handle_text_input("/repair")

    assert "requires a failed verification" in app.messages[-1]


def test_textual_repair_command_runs_manual_repair_after_failed_verification(tmp_path) -> None:
    runtime = FakeRuntime()
    failed_result = TerminalResult(
        command="python -m pytest",
        status="failed",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.SAFE, reason="safe", requires_user=False),
        exit_code=1,
        summary="assertion failed",
    )
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)
    app.last_manual_verification = ManualVerificationResult(
        run_id="verify-1",
        status="failed",
        changed_files=["src/calc.py"],
        commands=["python -m pytest"],
        verification_results=[failed_result],
        summary_path=".xhx/logbook/verify-1.md",
        risk_summary=["Verification failed."],
    )

    assert app.handle_text_input("/repair loop")

    assert len(runtime.repair_calls) == 1
    repair_call = runtime.repair_calls[0]
    assert repair_call["task"] == "manual repair"
    assert repair_call["changed_files"] == ["src/calc.py"]
    assert repair_call["failed_verification_results"] == [failed_result]
    assert repair_call["profile_name"] == "mock"
    assert repair_call["max_attempts"] == 2
    assert app.last_manual_repair is not None
    assert app.last_manual_repair.verification == "passed"
    assert "manual repair: success, verification: passed" in app.messages[-1]
