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
            assert "state: idle" in str(pilot.app.query_one("#statusline").content)
            assert len(pilot.app.query("#side")) == 0

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
            assert "verify: skipped_no_changes" in str(pilot.app.query_one("#statusline").content)

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
            assert "verify: passed" in str(pilot.app.query_one("#statusline").content)

    asyncio.run(run_app())
    assert "return a + b" in (workspace / "src" / "calc.py").read_text(encoding="utf-8")
    assert app.last_result is not None
    assert (workspace / app.last_result.summary_path).exists()


def test_textual_command_console_handles_sessions_and_resume(tmp_path) -> None:
    from xhx_agent.runtime.session import record_session
    from xhx_agent.runtime.app import RunResult

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    # 1. Test /sessions when empty
    assert app.handle_text_input("/sessions")
    assert "No sessions recorded" in app.messages[-1]

    # 2. Record a session
    result = RunResult(
        run_id="run-test-sessions-123",
        status="success",
        changed_files=["src/test.py"],
        commands=[],
        verification="passed",
        summary_path=".xhx/logbook/run-test-sessions-123.md",
        risk_summary=[],
    )
    record_session(tmp_path, "my test task", result)

    # 3. Test /sessions lists recorded session
    assert app.handle_text_input("/sessions")
    assert "1 个会话" in app.messages[-1]

    # 4. Test /resume with invalid ID
    assert app.handle_text_input("/resume non_existent_id")
    assert "Session 'non_existent_id' not found" in app.messages[-1]

    # 5. Test /resume with valid ID prefix
    assert app.handle_text_input("/resume ions-123")
    assert "已恢复会话" in app.messages[-1]
    assert app.last_result is not None
    assert app.last_result.run_id == "run-test-sessions-123"

    # 6. Test /sessions filtering
    # Clear messages for clarity
    app.messages.clear()
    assert app.handle_text_input("/sessions my")
    assert "1 个会话" in app.messages[-1]

    app.messages.clear()
    assert app.handle_text_input("/sessions nope")
    assert "No sessions matching 'nope' found" in app.messages[-1]

    # 7. Test /new alias
    assert app.handle_text_input("/new")
    assert not app.messages


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
            assert app.pending_confirmation.command == "python -m pytest"
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

    assert "Context 400/6k 6.7%" in app.messages[-1]
    assert app.active_detail == "context"
    assert "Context 400 / 6k (6.7%)" in app.detail_text
    assert "turn 2" in app.detail_text
    assert "选中文件 5" in app.detail_text
    assert "省略 1" in app.detail_text
    assert "预算 6k" in app.detail_text
    assert "压缩" not in app.detail_text

    # With compaction
    state.compaction_count = 3
    state.compaction_last_before = 24
    state.compaction_last_after = 9
    assert app.handle_text_input("/context")
    assert "已压缩 3 次" in app.detail_text
    assert "24→9 条" in app.detail_text

    # With budget <= 0
    state.context_budget_tokens = 0
    assert app.handle_text_input("/context")
    assert "Context —" in app.messages[-1]
    assert "Context —" in app.detail_text



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


def test_textual_mode_command_shows_selectable_picker_and_applies(tmp_path) -> None:
    """/mode with no argument shows an arrow-navigable picker; Enter applies the mode directly."""
    from textual.widgets import Input, OptionList

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            app.query_one("#input", Input).value = "/mode"
            await pilot.press("enter")
            await pilot.pause()

            # Picker shows exactly the three real paradigms; input is enabled and focused.
            options = app.query("#active_options")
            assert len(options) == 1
            option_list = app.query_one("#active_options", OptionList)
            assert option_list.option_count == 3
            assert not app.query_one("#input", Input).disabled
            assert app.query_one("#input", Input).has_focus

            # Arrow-key navigation, then Enter applies the highlighted mode directly.
            await pilot.press("down")
            await pilot.press("down")
            await pilot.pause()
            assert option_list.highlighted == 2
            await pilot.press("enter")
            await pilot.pause()

            assert app.state.mode == "graph"
            assert len(app.query("#active_options")) == 0
            assert not app.query_one("#input", Input).disabled
            assert "mode: graph" in app.messages[-1]

    import asyncio
    asyncio.run(run_app())


def test_textual_picker_navigation_wraps_around(tmp_path) -> None:
    """Every picker shares one wrap-around behaviour: up on the first jumps to the last, and
    down on the last returns to the first."""
    from textual.widgets import Input

    from xhx_agent.tui.textual_app import WrappingOptionList

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            app.query_one("#input", Input).value = "/mode"
            await pilot.press("enter")
            await pilot.pause()

            option_list = app.query_one("#active_options", WrappingOptionList)
            assert option_list.option_count == 3
            assert option_list.highlighted == 0

            # Up on the first option wraps to the last.
            await pilot.press("up")
            await pilot.pause()
            assert option_list.highlighted == 2

            # Down on the last option wraps back to the first.
            await pilot.press("down")
            await pilot.pause()
            assert option_list.highlighted == 0

    import asyncio
    asyncio.run(run_app())


def test_textual_threads_prior_messages_across_turns(tmp_path) -> None:
    """Regression: the model-facing conversation history is carried across turns (real memory),
    not just shown in the UI. Previously each turn only got a metadata follow-up summary."""
    from xhx_agent.runtime.session import save_transcript

    class MemoryRuntime:
        def __init__(self) -> None:
            self.seen_prior: list = []
            self.turn = 0

        def run_task(self, task, **kwargs):
            self.turn += 1
            self.seen_prior.append(kwargs.get("prior_messages"))
            run_id = f"run-{self.turn}"
            prior = kwargs.get("prior_messages") or [{"role": "system", "content": "sys"}]
            messages = list(prior) + [
                {"role": "user", "content": task},
                {"role": "assistant", "content": f"answer {self.turn}"},
            ]
            rel = save_transcript(tmp_path, run_id, messages)
            return RunResult(
                run_id=run_id, status="success", changed_files=[], commands=[],
                verification="not_executed", summary_path=f".xhx/logbook/{run_id}.md",
                risk_summary=[], answer=f"answer {self.turn}", transcript_path=rel, mode="loop",
            )

    runtime = MemoryRuntime()
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    # First turn: no prior memory yet; it gets recorded from this run's transcript.
    app.run_task("first question")
    assert runtime.seen_prior[0] is None
    assert app.prior_messages is not None
    assert any(m.get("content") == "first question" for m in app.prior_messages)

    # Second turn: the model actually receives the accumulated conversation.
    app.run_task("second question")
    second_prior = runtime.seen_prior[1]
    assert second_prior is not None
    assert any(m.get("content") == "first question" for m in second_prior)
    assert any(m.get("content") == "answer 1" for m in second_prior)


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
    assert "/python-debugger" in app.messages[-1]


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
    state.context_used_tokens_estimate = 400
    state.context_budget_tokens = 6000
    state.verification = "running"
    state.changed_files = ["a.py", "b.py"]
    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 100, "completion": 20, "total": 120, "cumulative_total": 120},
        )
    )
    state.is_streaming = True  # type: ignore[attr-defined]

    snapshot = TextualSnapshot.from_state(
        state, workspace="/repo", profile="mock", auto_repair=False, assume_yes=True
    )
    assert "state: running" in snapshot.status_line
    assert "mode: loop" in snapshot.status_line
    assert "turn: 3" in snapshot.status_line
    assert "tokens: 120" in snapshot.status_line
    assert "[green]Context 400/6k 6.7%[/green]" in snapshot.status_line
    assert "verify: running" in snapshot.status_line
    assert "changed: 2" in snapshot.status_line
    assert "streaming: yes" in snapshot.status_line

    state.is_streaming = False  # type: ignore[attr-defined]
    snapshot_non_streaming = TextualSnapshot.from_state(
        state, workspace="/repo", profile="mock", auto_repair=False, assume_yes=True
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


def test_textual_snapshot_does_not_duplicate_answer_when_history_present() -> None:
    """Regression: with history present, the conversation must not also reconstruct the
    answer/task from state (which previously showed both `model>` and `assistant>`)."""
    state = ConsoleState()
    # State as it looks right after a finished run.
    state.task = "你好"
    state.status = "success"
    state.plan_summary = "loop answer [turn 1]"
    state.model_output = "你好！有什么我可以帮你的吗？"
    state.is_streaming = False  # type: ignore[attr-defined]
    state.summary_path = ".xhx/logbook/run-1.md"
    # Append-only history is the single source of truth.
    state.textual_messages = [  # type: ignore[attr-defined]
        "user> 你好",
        "summary> .xhx/logbook/run-1.md",
        "assistant> 你好！有什么我可以帮你的吗？",
        "system> run finished: success, verification: not_executed",
    ]

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )

    # The answer and the user task each appear exactly once; no `model>`/`plan>` reconstruction leaks in.
    assert snapshot.conversation.count("你好！有什么我可以帮你的吗？") == 1
    assert snapshot.conversation.count("user> 你好") == 1
    assert "model>" not in snapshot.conversation
    assert "plan>" not in snapshot.conversation


def test_textual_snapshot_shows_streaming_line_atop_history() -> None:
    """While streaming, the in-flight line is shown after committed history without duplicating it."""
    state = ConsoleState()
    state.model_output = "import os"
    state.is_streaming = True  # type: ignore[attr-defined]
    state.textual_messages = ["user> add import"]  # type: ignore[attr-defined]

    snapshot = TextualSnapshot.from_state(
        state,
        workspace="/repo",
        profile="mock",
        auto_repair=False,
        assume_yes=True,
    )

    assert "user> add import" in snapshot.conversation
    assert "model (streaming…)> import os▌" in snapshot.conversation


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


def test_textual_app_clipboard_falls_back_and_overrides(tmp_path, monkeypatch) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    # If get_clipboard_text returns None, it falls back to internal clipboard
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_clipboard_text", lambda: None)
    app._clipboard = "internal text"
    assert app.clipboard == "internal text"

    # If get_clipboard_text returns some text, it uses it and replaces newlines
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_clipboard_text", lambda: "external\r\ntext")
    assert app.clipboard == "external text"


def test_textual_app_interactive_model_selection(tmp_path, monkeypatch) -> None:
    from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile

    fake_profiles = ProfilesFile(
        default_profile="mock1",
        profiles=[
            ModelProfile(name="mock1", provider="openai-compatible", model="gpt-4"),
            ModelProfile(name="mock2", provider="mock", model="deepseek-chat"),
        ]
    )
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_profiles", lambda ws: fake_profiles)

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock1")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "m", "o", "d", "e", "l", "enter")
            await pilot.pause()

            from textual.widgets import OptionList, Input
            active_options = pilot.app.query_one("#active_options", OptionList)
            assert active_options is not None
            assert not active_options.has_focus
            assert not pilot.app.query_one("#input", Input).disabled
            assert pilot.app.query_one("#input", Input).has_focus

            await pilot.press("down", "enter")
            await pilot.pause()

            assert len(pilot.app.query("#active_options")) == 0
            assert not pilot.app.query_one("#input", Input).disabled
            assert pilot.app.profile == "mock2"

    import asyncio
    asyncio.run(run_app())


def test_textual_app_interactive_session_selection(tmp_path) -> None:
    from xhx_agent.runtime.session import record_session
    from xhx_agent.runtime.app import RunResult

    result1 = RunResult(
        run_id="run-1",
        status="success",
        changed_files=[],
        commands=[],
        verification="passed",
        summary_path="",
        risk_summary=[],
    )
    result2 = RunResult(
        run_id="run-2",
        status="success",
        changed_files=[],
        commands=[],
        verification="passed",
        summary_path="",
        risk_summary=[],
    )
    record_session(tmp_path, "task 1", result1)
    record_session(tmp_path, "task 2", result2)

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "s", "e", "s", "s", "i", "o", "n", "s", "enter")
            await pilot.pause()

            from textual.widgets import OptionList, Input
            active_options = pilot.app.query_one("#active_options", OptionList)
            assert active_options is not None
            assert not active_options.has_focus
            assert pilot.app.query_one("#input", Input).has_focus
            assert active_options.get_option_at_index(0).id == "run-2"

            await pilot.press("enter")
            await pilot.pause()

            assert len(pilot.app.query("#active_options")) == 0
            assert pilot.app.last_result is not None
            assert pilot.app.last_result.run_id == "run-2"
            assert pilot.app.active_detail == "overview"

    import asyncio
    asyncio.run(run_app())


def test_textual_app_interactive_permission_confirmation(tmp_path) -> None:
    from xhx_agent.tui.textual_app import PendingConfirmation
    from xhx_agent.safety.policy import PolicyDecision
    from xhx_agent.safety.risk import RiskLevel

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            decision = PolicyDecision(
                decision="confirm",
                risk=RiskLevel.CONFIRM,
                reason="test confirm",
                requires_user=True,
            )
            confirmation = PendingConfirmation(command="python test.py", decision=decision)

            pilot.app.open_pending_confirmation(confirmation)
            await pilot.pause()

            from textual.widgets import OptionList, Input
            active_options = pilot.app.query_one("#active_options", OptionList)
            assert active_options is not None
            assert not active_options.has_focus
            assert pilot.app.query_one("#input", Input).has_focus
            assert active_options.get_option_at_index(0).id == "allow"
            assert active_options.get_option_at_index(1).id == "deny"

            await pilot.press("enter")
            await pilot.pause()

            assert confirmation.event.is_set()
            assert confirmation.response is True
            assert len(pilot.app.query("#active_options")) == 0

    import asyncio
    asyncio.run(run_app())


def test_textual_app_interactive_selection_escape(tmp_path, monkeypatch) -> None:
    from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile

    fake_profiles = ProfilesFile(
        default_profile="mock1",
        profiles=[
            ModelProfile(name="mock1", provider="openai-compatible", model="gpt-4"),
            ModelProfile(name="mock2", provider="mock", model="deepseek-chat"),
        ]
    )
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_profiles", lambda ws: fake_profiles)

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock1")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/", "m", "o", "d", "e", "l", "enter")
            await pilot.pause()

            from textual.widgets import OptionList, Input
            assert pilot.app.query_one("#active_options", OptionList) is not None

            await pilot.press("escape")
            await pilot.pause()

            assert len(pilot.app.query("#active_options")) == 0
            input_widget = pilot.app.query_one("#input", Input)
            assert not input_widget.disabled
            assert input_widget.has_focus

    import asyncio
    asyncio.run(run_app())


def test_textual_app_resume_loads_transcript_messages(tmp_path) -> None:
    from xhx_agent.runtime.session import record_session
    from xhx_agent.runtime.app import RunResult

    fake_messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello world"},
        {"role": "assistant", "content": "hi there"},
    ]

    result = RunResult(
        run_id="run-res-abc",
        status="success",
        changed_files=[],
        commands=[],
        verification="passed",
        summary_path="",
        risk_summary=[],
    )
    object.__setattr__(result, "messages", fake_messages)
    record_session(tmp_path, "test task", result)

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    app.active_detail = "sessions"
    app.detail_text = "stale sessions details"
    app.handle_resume("run-res-abc")

    assert "user> hello world" in app.messages
    assert "assistant> hi there" in app.messages
    assert not any("system prompt" in m for m in app.messages)
    assert app.state.task == "test task"
    assert app.active_detail == "overview"
    assert "Use /plan" in app.detail_text


def test_textual_app_interactive_command_selection(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            await pilot.click("#input")
            await pilot.press("/")
            await pilot.pause()

            from textual.widgets import OptionList, Input
            active_options = pilot.app.query_one("#active_options", OptionList)
            assert active_options is not None
            assert not active_options.has_focus
            assert not pilot.app.query_one("#input", Input).disabled

            # First option is /help, press enter
            await pilot.press("enter")
            await pilot.pause()

            assert len(pilot.app.query("#active_options")) == 0
            input_widget = pilot.app.query_one("#input", Input)
            assert not input_widget.disabled
            assert input_widget.value == "/help"

            # Clear value and type / again to select a command with arguments
            input_widget.value = ""
            await pilot.press("/")
            await pilot.pause()

            active_options = pilot.app.query_one("#active_options", OptionList)
            assert active_options is not None
            assert active_options.highlighted == 0

            # Navigate down
            await pilot.press("down")
            await pilot.pause()
            assert active_options.highlighted == 1

            # Navigate back up
            await pilot.press("up")
            await pilot.pause()
            assert active_options.highlighted == 0

            pilot.app.resolve_interactive_selection("/resume")
            await pilot.pause()

            assert input_widget.value == "/resume "

    import asyncio
    asyncio.run(run_app())


def test_textual_app_input_focus_retention(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    async def run_app() -> None:
        async with app.run_test() as pilot:
            from textual.widgets import Input
            input_widget = pilot.app.query_one("#input", Input)
            # Verify it has focus initially
            assert input_widget.has_focus

            # Try to blur input by focusing another widget
            other = pilot.app.query_one("#conversation")
            other.focus()
            await pilot.pause()

            # Focus should be forced back to input
            assert input_widget.has_focus

    import asyncio
    asyncio.run(run_app())


def test_textual_app_state_turn_and_streaming_delta() -> None:
    from xhx_agent.tui.state import ConsoleState
    from xhx_agent.runtime.events import RuntimeEvent

    state = ConsoleState()
    assert state.context_turn is None
    assert state.model_delta_count == 0
    assert not state.is_streaming

    # Reduce tool_start event with turn
    state.reduce(RuntimeEvent(type="tool_start", message="Tool started", payload={"tool": "search", "turn": 3}))
    assert state.context_turn == 3
    assert not state.is_streaming

    # Reduce model_delta event with turn
    state.reduce(RuntimeEvent(type="model_delta", message="hello", payload={"turn": 4}))
    assert state.context_turn == 4
    assert state.model_delta_count == 1
    assert state.is_streaming is True

    # Reduce another event to stop streaming
    state.reduce(RuntimeEvent(type="tool_start", message="Tool started", payload={"tool": "read_file"}))
    assert state.is_streaming is False
    assert state.context_turn == 4


def test_state_reduce_token_usage_tracks_cumulative_total() -> None:
    from xhx_agent.tui.state import ConsoleState
    from xhx_agent.runtime.events import RuntimeEvent

    state = ConsoleState()
    assert state.tokens_total == 0

    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 10, "completion": 6, "total": 16, "cumulative_total": 16},
        )
    )
    assert state.tokens_prompt == 10
    assert state.tokens_completion == 6
    assert state.tokens_total == 16

    state.reduce(
        RuntimeEvent(
            type="token_usage",
            message="Token usage updated.",
            payload={"prompt": 8, "completion": 8, "total": 16, "cumulative_total": 32},
        )
    )
    assert state.tokens_total == 32


def test_textual_timeline_translates_runtime_events_into_messages(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    app.handle_runtime_event(
        RuntimeEvent(type="tool_start", message="", payload={"tool": "search", "turn": 1})
    )
    app.handle_runtime_event(
        RuntimeEvent(
            type="tool_result",
            message="",
            payload={"tool": "search", "status": "success", "summary": "0 hits", "turn": 1},
        )
    )
    app.handle_runtime_event(
        RuntimeEvent(type="graph_review", message="round 1: passed", payload={"round": 1})
    )
    app.handle_runtime_event(
        RuntimeEvent(type="verification_start", message="", payload={"command": "python -m pytest"})
    )
    app.handle_runtime_event(
        RuntimeEvent(
            type="verification_result",
            message="",
            payload={"command": "python -m pytest", "status": "failed", "exit_code": 1},
        )
    )

    joined = "\n".join(app.messages)
    assert "⟶ tool  search" in joined
    assert "✓ tool  search → 0 hits" in joined
    assert "▸ agent  review  round 1: passed" in joined
    assert "⚙ verify  python -m pytest" in joined
    assert "→ failed(exit 1)" in joined


def test_textual_timeline_skips_non_visible_events(tmp_path) -> None:
    """run_start/run_end/cancel_requested 等已有专门处理或不该进时间线，避免打乱 messages 索引。"""
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    before = len(app.messages)
    app.handle_runtime_event(
        RuntimeEvent(type="run_start", message="", payload={"run_id": "r1", "task": "t", "profile": "mock"})
    )
    app.handle_runtime_event(RuntimeEvent(type="model_delta", message="hello", payload={"turn": 1}))
    assert len(app.messages) == before


def test_textual_action_cancel_task(tmp_path) -> None:
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")
    assert not app.is_task_running()

    app.action_cancel_task()
    assert "Use /exit to quit the console." in app.messages[-1]

    state = ConsoleState()
    state.status = "running_tool"
    app_running = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", state=state)
    assert app_running.is_task_running()

    app_running.action_cancel_task()
    assert app_running.is_cancel_requested() is True


def test_textual_auto_memory_suggests_and_writes_on_success(tmp_path, monkeypatch) -> None:
    from xhx_agent.memory.store import MemoryRecord

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())

    # 把客户端搭建链路 stub 掉（否则 mock+tmp_path 下 get_profile/build_chat_client 可能抛异常被
    # _maybe_suggest_memories 的 try/except 吞掉，导致拿不到写入而假失败）。
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_config", lambda ws: type("C", (), {"default_profile": "mock"})())
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_profile", lambda ws, name: object())
    monkeypatch.setattr("xhx_agent.tui.textual_app.build_chat_client", lambda profile: object())

    # 注入一个候选记忆，并强制确认返回 True（绕开交互 picker，直接测“成功→提议→写入”链路）。
    cand = MemoryRecord(name="test-fact", description="a fact", mtype="project", body="remember me")
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.propose_memories",
        lambda client, task, digest, existing_names=None: [cand],
    )
    written = {}
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.write_memory",
        lambda workspace, *, name, description, mtype, body: written.update(
            {"name": name, "mtype": mtype, "body": body}
        ),
    )
    monkeypatch.setattr(TextualCommandConsoleApp, "_confirm_memory_blocking", lambda self, c: True)

    app.run_task("do something", announce_user=False, reset_cancel=False)

    assert written.get("name") == "test-fact"
    assert any("Remembered: test-fact" in m for m in app.messages)


def test_textual_auto_memory_skips_when_declined(tmp_path, monkeypatch) -> None:
    from xhx_agent.memory.store import MemoryRecord

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=FakeRuntime())
    monkeypatch.setattr("xhx_agent.tui.textual_app.load_config", lambda ws: type("C", (), {"default_profile": "mock"})())
    monkeypatch.setattr("xhx_agent.tui.textual_app.get_profile", lambda ws, name: object())
    monkeypatch.setattr("xhx_agent.tui.textual_app.build_chat_client", lambda profile: object())
    cand = MemoryRecord(name="test-fact", description="a fact", mtype="project", body="x")
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.propose_memories",
        lambda client, task, digest, existing_names=None: [cand],
    )
    calls = {"wrote": False}
    monkeypatch.setattr(
        "xhx_agent.tui.textual_app.write_memory",
        lambda *a, **k: calls.update(wrote=True),
    )
    monkeypatch.setattr(TextualCommandConsoleApp, "_confirm_memory_blocking", lambda self, c: False)

    app.run_task("do something", announce_user=False, reset_cancel=False)

    assert calls["wrote"] is False



def test_textual_run_task_saves_complete_view_log_at_turn_end(tmp_path) -> None:
    """T4 时序回归：view-log 必须在本轮 apply_run_result + run-finished 之后落盘，
    因而包含本轮完整界面（开场 user 行 + 结尾 run-finished 行），且 SessionEntry
    带上 view_path 与 turn_count。若 record/落盘仍在 apply_run_result 之前，结尾行会缺失。"""
    from xhx_agent.runtime.session import load_session, load_view_log

    runtime = FakeRuntime()  # run_task 返回 run_id="run-1"
    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock", runtime=runtime)

    app.run_task("fix tests")

    entry = load_session(tmp_path, "run-1")
    assert entry is not None
    assert entry.view_path == ".xhx/sessions/run-1.view.json"
    assert entry.turn_count == 1

    view = load_view_log(tmp_path, entry.view_path)
    assert view is not None
    # 开场行在前、结尾行在后 —— 证明落盘发生在本轮收尾之后。
    assert "user> fix tests" in view
    assert any(line.startswith("system> run finished:") for line in view)


def test_textual_app_sessions_clear(tmp_path) -> None:
    from xhx_agent.runtime.session import record_session
    from xhx_agent.runtime.app import RunResult

    app = TextualCommandConsoleApp(workspace=tmp_path, profile="mock")

    # Record 1 with view_path, 1 without view_path
    res = RunResult(
        run_id="run-1",
        status="success",
        changed_files=[],
        commands=[],
        verification="passed",
        summary_path="",
        risk_summary=[],
    )
    record_session(tmp_path, "task 1", res, view_path=".xhx/sessions/run-1.view.json")
    res.run_id = "run-2"
    record_session(tmp_path, "task 2", res, view_path=None)

    # Invoke /sessions clear
    assert app.handle_text_input("/sessions clear")
    assert "已清理 1 条旧会话" in app.messages[-1]








