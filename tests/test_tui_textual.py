import asyncio
import shutil
import threading
from pathlib import Path

from xhx_agent.runtime.app import (
    DiffSummary,
    ManualRepairResult,
    ManualVerificationResult,
    PlanPreview,
    RunResult,
    RuntimeApp,
)
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile, profiles_path
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.tui.state import ConsoleState
from xhx_agent.tui.textual_app import TextualCommandConsoleApp, TextualSnapshot


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
    state.reduce(
        RuntimeEvent(type="model_plan", message="Patch failing test", payload={"status": "continue", "step_count": 1})
    )
    state.reduce(
        RuntimeEvent(
            type="run_end",
            message="Run finished.",
            payload={
                "status": "success",
                "verification": "passed",
                "changed_files": ["src/calc.py"],
                "summary_path": ".xhx/logbook/run-1.md",
            },
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
    assert "overview" in snapshot.details
    assert "/help /model /status" in snapshot.commands


def test_textual_snapshot_shows_pending_steer_cancel_and_permission_state() -> None:
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
    state.reduce(
        RuntimeEvent(type="cancel_requested", message="Steer requested by user.", payload={"source": "textual"})
    )

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="D:/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=False,
        pending_steer="change direction",
        next_confirm_response=True,
        pending_confirmation="python -m pytest (confirm)",
    )

    assert "pending steer: change direction" in snapshot.runtime_state
    assert "cancel: requested" in snapshot.runtime_state
    assert "next confirm: allow once" in snapshot.runtime_state
    assert "waiting: python -m pytest (confirm)" in snapshot.runtime_state


def test_textual_command_console_app_can_render_initial_shell(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            assert app.title.startswith("xhx-agent | idle | profile: mock")
            assert "No conversation yet." in str(pilot.app.query_one("#conversation").content)
            assert "changed files:" in str(pilot.app.query_one("#changed").content)
            assert "details:" in str(pilot.app.query_one("#details").content)

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
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_start", message="Run started.", payload={"run_id": "run-1", "task": task, "profile": "mock"}
            )
        )
        kwargs["event_callback"](
            RuntimeEvent(type="model_plan", message="Analyze task", payload={"status": "done", "step_count": 0})
        )
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_end",
                message="Run finished.",
                payload={
                    "run_id": "run-1",
                    "status": "success",
                    "verification": "skipped_no_changes",
                    "changed_files": [],
                    "summary_path": ".xhx/logbook/run-1.md",
                },
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
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_start",
                message="Manual verification started.",
                payload={"run_id": "verify-1", "task": "manual verification", "profile": "manual"},
            )
        )
        kwargs["event_callback"](
            RuntimeEvent(
                type="verification_start", message="Verification started.", payload={"command": "python -m pytest"}
            )
        )
        allowed = kwargs["confirm_callback"](
            "python -m pytest",
            PolicyDecision(
                decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True
            ),
        )
        status = "success" if allowed else "confirm"
        kwargs["event_callback"](
            RuntimeEvent(
                type="verification_result",
                message="Verification finished.",
                payload={"command": "python -m pytest", "status": status, "exit_code": 0 if allowed else None},
            )
        )
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
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_start",
                message="Manual repair started.",
                payload={"run_id": "repair-1", "task": kwargs["task"], "profile": "mock"},
            )
        )
        kwargs["event_callback"](
            RuntimeEvent(
                type="repair_start",
                message="Manual repair attempt started.",
                payload={"attempt": 1, "max_attempts": kwargs["max_attempts"]},
            )
        )
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_end",
                message="Manual repair finished.",
                payload={
                    "run_id": "repair-1",
                    "status": "success",
                    "verification": "passed",
                    "changed_files": kwargs["changed_files"],
                    "summary_path": ".xhx/logbook/repair-1.md",
                },
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


class BlockingRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def run_task(self, task, **kwargs):
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().run_task(task, **kwargs)


class BlockingVerifyRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.confirm_seen = threading.Event()
        self.verify_finished = threading.Event()

    def verify_changed_files(self, changed_files, **kwargs):
        self.verify_calls.append((changed_files, kwargs))
        kwargs["event_callback"](
            RuntimeEvent(
                type="run_start",
                message="Manual verification started.",
                payload={"run_id": "verify-1", "task": "manual verification", "profile": "manual"},
            )
        )
        kwargs["event_callback"](
            RuntimeEvent(
                type="verification_start", message="Verification started.", payload={"command": "python -m pytest"}
            )
        )
        self.confirm_seen.set()
        allowed = kwargs["confirm_callback"](
            "python -m pytest",
            PolicyDecision(
                decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True
            ),
        )
        status = "success" if allowed else "confirm"
        kwargs["event_callback"](
            RuntimeEvent(
                type="verification_result",
                message="Verification finished.",
                payload={"command": "python -m pytest", "status": status, "exit_code": 0 if allowed else None},
            )
        )
        self.verify_finished.set()
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
    app.state.reduce(
        RuntimeEvent(
            type="run_start", message="Run started.", payload={"run_id": "run-1", "task": "fix", "profile": "mock"}
        )
    )

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
            await pilot.pause()
            assert "summary> .xhx/logbook/run-1.md" in str(pilot.app.query_one("#conversation").content)
            assert "verification: skipped_no_changes" in str(pilot.app.query_one("#runtime").content)

    import asyncio

    asyncio.run(run_app())


def test_textual_submitted_task_uses_background_worker(tmp_path) -> None:
    runtime = BlockingRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("f", "i", "x", "enter")
            assert runtime.started.wait(timeout=2)
            assert app.state.status == "running"
            assert app.state.task == "fix"
            assert any(worker.name == "runtime-task" for worker in app.workers)
            runtime.release.set()
            await pilot.pause()
            assert app.last_result is not None
            assert app.last_result.status == "success"

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


def test_textual_submitted_verify_can_wait_for_permission(tmp_path) -> None:
    runtime = BlockingVerifyRuntime()
    state = ConsoleState()
    state.changed_files = ["src/calc.py"]
    app = TextualCommandConsoleApp(
        workspace=tmp_path, profile="mock", runtime=runtime, state=state, permission_timeout_seconds=2
    )

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "v", "e", "r", "i", "f", "y", "enter")
            assert runtime.confirm_seen.wait(timeout=2)
            await pilot.pause()
            assert app.pending_confirmation is not None
            assert any(worker.name == "manual-verification" for worker in app.workers)
            assert app.handle_text_input("/allow")
            for _ in range(50):
                if runtime.verify_finished.is_set():
                    break
                await asyncio.sleep(0.02)
            assert runtime.verify_finished.is_set()
            assert app.last_manual_verification is not None
            assert app.last_manual_verification.status == "passed"

    import asyncio

    asyncio.run(run_app())


def test_textual_fullscreen_runs_real_runtime_python_fixture_with_permission(tmp_path, monkeypatch) -> None:
    fixture = Path(__file__).parent / "fixtures" / "python_bug"
    workspace = tmp_path / "python_bug"
    shutil.copytree(fixture, workspace)
    RuntimeApp(workspace).init_project()
    confirm_decision = PolicyDecision(
        decision="confirm",
        risk=RiskLevel.CONFIRM,
        reason="Confirm test command.",
        requires_user=True,
    )
    allow_decision = PolicyDecision(
        decision="allow",
        risk=RiskLevel.CONFIRM,
        reason="Command allowed by policy.",
        requires_user=False,
    )

    def fake_run_terminal(_workspace, command, assume_yes=False, _timeout_seconds=120, confirm_callback=None):
        if not assume_yes and confirm_callback is not None and not confirm_callback(command, confirm_decision):
            return TerminalResult(
                command=command,
                status="confirm",
                policy=confirm_decision,
                summary="User declined command confirmation.",
            )
        return TerminalResult(
            command=command,
            status="success",
            policy=allow_decision,
            exit_code=0,
            summary="passed",
        )

    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", fake_run_terminal)
    app = TextualCommandConsoleApp(workspace=workspace, profile="mock", permission_timeout_seconds=10)
    app.state.mode = "linear"

    async def run_app() -> None:
        async with app.run_test() as pilot:
            # Let the app finish its initial compose/mount before the background worker starts,
            # so run_task can resolve "#conversation" instead of racing mount on a fast runner.
            await pilot.pause()
            assert app.handle_text_input("fix failing test", use_worker=True)
            for _ in range(200):
                if app.pending_confirmation is not None:
                    break
                await asyncio.sleep(0.05)
            assert app.pending_confirmation is not None
            assert "pytest" in app.pending_confirmation.command
            assert app.handle_text_input("/allow")
            for _ in range(200):
                if app.last_result is not None:
                    break
                await asyncio.sleep(0.05)
            assert app.last_result is not None
            assert app.last_result.status == "success"
            assert app.last_result.verification == "passed"
            assert app.last_result.changed_files == ["src/calc.py"]
            assert "verification: passed" in str(pilot.app.query_one("#runtime").content)

    asyncio.run(run_app())
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    assert app.last_result is not None
    assert (workspace / app.last_result.summary_path).exists()


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


def test_textual_permission_confirmation_defaults_to_decline_without_fullscreen(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    allowed = app.confirm_terminal_command(
        "python -m pytest",
        PolicyDecision(decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True),
    )

    assert allowed is False
    assert "declined" in app.messages[-1]


def test_textual_fullscreen_permission_can_wait_for_allow(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", permission_timeout_seconds=2)
    decision = PolicyDecision(
        decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True
    )
    result: dict[str, bool] = {}

    async def run_app() -> None:
        async with app.run_test() as pilot:
            worker = threading.Thread(
                target=lambda: result.update(allowed=app.confirm_terminal_command("python -m pytest", decision))
            )
            worker.start()
            await pilot.pause()
            assert app.pending_confirmation is not None
            assert "permission required" in app.messages[-1]
            assert "waiting: python -m pytest (confirm)" in str(pilot.app.query_one("#runtime").content)
            assert app.handle_text_input("/allow")
            for _ in range(50):
                if result:
                    break
                await asyncio.sleep(0.02)
            assert result == {"allowed": True}
            worker.join(timeout=2)
            assert app.pending_confirmation is None
            assert "permission allowed" in app.messages[-1]

    import asyncio

    asyncio.run(run_app())


def test_textual_fullscreen_permission_can_wait_for_deny(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", permission_timeout_seconds=2)
    decision = PolicyDecision(
        decision="confirm", risk=RiskLevel.CONFIRM, reason="Confirm test command.", requires_user=True
    )
    result: dict[str, bool] = {}

    async def run_app() -> None:
        async with app.run_test() as pilot:
            worker = threading.Thread(
                target=lambda: result.update(allowed=app.confirm_terminal_command("python -m pytest", decision))
            )
            worker.start()
            await pilot.pause()
            assert app.pending_confirmation is not None
            assert app.handle_text_input("/deny")
            for _ in range(50):
                if result:
                    break
                await asyncio.sleep(0.02)
            assert result == {"allowed": False}
            worker.join(timeout=2)
            assert app.pending_confirmation is None
            assert "permission declined" in app.messages[-1]

    import asyncio

    asyncio.run(run_app())


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
    assert app.active_detail == "context"
    assert "budget: 400/6000" in app.detail_text


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
    assert app.active_detail == "evidence"
    assert "python -m pytest" in app.detail_text


def test_textual_diff_command_uses_runtime_read_only_summary(tmp_path) -> None:
    runtime = FakeRuntime()
    state = ConsoleState()
    state.changed_files = ["src/calc.py"]
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime, state=state)

    assert app.handle_text_input("/diff")

    assert runtime.diff_calls == [["src/calc.py"]]
    assert "1 changed file(s)." in app.messages[-1]
    assert "+return a + b" in app.messages[-1]
    assert app.active_detail == "diff"
    assert "+return a + b" in app.detail_text


def test_textual_plan_command_previews_task_through_runtime(tmp_path) -> None:
    runtime = FakeRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    assert app.handle_text_input("/plan analyze repo")

    assert runtime.plan_calls == [("analyze repo", "mock")]
    assert "plan preview: success" in app.messages[-1]
    assert "Preview analyze repo" in app.messages[-1]
    assert "steps=2" in app.messages[-1]
    assert app.active_detail == "plan"
    assert "trace: .xhx/traces/dry-run-1.jsonl" in app.detail_text


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


def test_textual_model_command_lists_and_switches_profiles(tmp_path) -> None:
    profiles_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(name="mock", provider="mock", base_url="", api_key_env="", model="mock", stream=False),
                ModelProfile(name="local", provider="openai-compatible", model="qwen-plus"),
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/model")

    assert "profiles:" in app.messages[-1]
    assert "mock* [mock/mock]" in app.messages[-1]
    assert "local [openai-compatible/qwen-plus]" in app.messages[-1]

    assert app.handle_text_input("/model local")

    assert app.profile == "local"
    assert "active profile: local" in app.messages[-1]


def test_textual_skills_command_lists_local_skill_dirs(tmp_path) -> None:
    skill_dir = tmp_path / ".xhx" / "skills" / "python-debugger"
    skill_dir.mkdir(parents=True)
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/skills")

    assert "skills:" in app.messages[-1]
    assert ".xhx/skills/python-debugger" in app.messages[-1]


def test_textual_dashboard_command_prints_state_summary(tmp_path) -> None:
    state = ConsoleState()
    state.status = "running_tool"
    state.run_id = "run-1"
    state.changed_files = ["src/calc.py"]
    state.context_used_tokens_estimate = 120
    state.context_budget_tokens = 6000
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", state=state)

    assert app.handle_text_input("/dashboard")

    assert "dashboard:" in app.messages[-1]
    assert "status=running_tool" in app.messages[-1]
    assert "run=run-1" in app.messages[-1]
    assert "changed=1" in app.messages[-1]
    assert app.active_detail == "dashboard"
    assert "pending_confirm: none" in app.detail_text


def test_textual_cancel_sets_state_for_running_task(tmp_path) -> None:
    state = ConsoleState()
    state.status = "running_tool"
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", state=state)

    assert app.handle_text_input("/cancel")

    assert app.is_cancel_requested() is True
    assert app.state.status == "cancelling"
    assert "Cancel requested" in app.messages[-1]


def test_textual_cancel_without_running_task_is_noop(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/cancel")

    assert app.is_cancel_requested() is False
    assert "No running task to cancel" in app.messages[-1]


def test_textual_live_command_reports_rich_only_boundary(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    assert app.handle_text_input("/live")

    assert "live: rich-only" in app.messages[-1]


def test_textual_builds_follow_up_task_from_last_result(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    assert app.build_runtime_task("first task") == "first task"
    app.last_result = RunResult(
        run_id="run-1",
        status="success",
        changed_files=["src/calc.py"],
        commands=["python -m pytest"],
        verification="passed",
        summary_path=".xhx/logbook/run-1.md",
        risk_summary=[],
    )

    follow_up = app.build_runtime_task("now update docs")

    assert "Follow-up task in the same console session." in follow_up
    assert "User request:\nnow update docs" in follow_up
    assert "- run_id: run-1" in follow_up
    assert "- verification: passed" in follow_up
    assert "- summary: .xhx/logbook/run-1.md" in follow_up


def test_textual_run_task_uses_follow_up_context(tmp_path) -> None:
    runtime = FakeRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)
    app.last_result = RunResult(
        run_id="run-1",
        status="success",
        changed_files=["src/calc.py"],
        commands=[],
        verification="skipped_no_changes",
        summary_path=".xhx/logbook/run-1.md",
        risk_summary=[],
    )

    app.run_task("continue")

    task, _kwargs = runtime.calls[0]
    assert "Follow-up task in the same console session." in task
    assert "User request:\ncontinue" in task


def test_textual_running_input_queues_steer_and_requests_cancel(tmp_path) -> None:
    runtime = FakeRuntime()
    state = ConsoleState()
    state.status = "running_tool"
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime, state=state)

    assert app.handle_text_input("change direction")

    assert runtime.calls == []
    assert app.pending_steer == "change direction"
    assert app.is_cancel_requested() is True
    assert app.state.status == "cancelling"
    assert "steer queued" in app.messages[-2]
    assert "Cancel requested" in app.messages[-1]


def test_textual_run_task_executes_queued_steer_as_follow_up(tmp_path) -> None:
    runtime = FakeRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)
    app.pending_steer = "follow the new direction"

    app.run_task("first task")

    assert len(runtime.calls) == 2
    first_task, first_kwargs = runtime.calls[0]
    second_task, second_kwargs = runtime.calls[1]
    assert first_task == "first task"
    assert "Follow-up task in the same console session." in second_task
    assert "User request:\nfollow the new direction" in second_task
    assert first_kwargs["cancel_check"]() is False
    assert second_kwargs["cancel_check"]() is False
    assert app.pending_steer is None
    assert "running queued steer as follow-up" in app.messages


def test_textual_apply_run_result_surfaces_loop_answer(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    result = RunResult(
        run_id="run-1",
        status="success",
        changed_files=[],
        commands=[],
        verification="not_executed",
        summary_path=".xhx/logbook/run-1.md",
        risk_summary=[],
        mode="loop",
        answer="loop 的回答",
    )

    app._apply_run_result(result)

    assert any("loop 的回答" in message for message in app.messages)


def test_textual_snapshot_status_line() -> None:
    state = ConsoleState()
    state.status = "running"
    state.mode = "loop"
    state.context_turn = 3
    state.model_delta_count = 120
    state.is_streaming = True  # type: ignore[attr-defined]

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )
    assert "state: running" in snapshot.status_line
    assert "mode: loop" in snapshot.status_line
    assert "turn: 3" in snapshot.status_line
    assert "tokens: 120" in snapshot.status_line
    assert "streaming: yes" in snapshot.status_line

    state.is_streaming = False  # type: ignore[attr-defined]
    snapshot_non_streaming = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )
    assert "streaming: no" in snapshot_non_streaming.status_line


def test_textual_snapshot_streaming_conversation() -> None:
    state = ConsoleState()
    state.task = "fix coding bug"
    state.model_output = "import os"
    state.is_streaming = True  # type: ignore[attr-defined]

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )
    assert "model (streaming…)> import os▌" in snapshot.conversation

    state.is_streaming = False  # type: ignore[attr-defined]
    snapshot_non_streaming = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )
    assert "model> import os" in snapshot_non_streaming.conversation
    assert "▌" not in snapshot_non_streaming.conversation


def test_textual_statusline_widget(tmp_path) -> None:
    from textual.widgets import Static

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            statusline_widget = pilot.app.query_one("#statusline", Static)
            assert statusline_widget is not None
            pilot.app.refresh_snapshot()
            assert "state: idle" in str(statusline_widget.content)

    import asyncio
    asyncio.run(run_app())
