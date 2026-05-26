from pathlib import Path

from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.models.types import ModelPlan, ToolStep
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.events import RuntimeEvent
from xhx_agent.runtime.profiles import ModelProfile, ProfilesFile, profiles_path
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel


def _console() -> Console:
    return Console(record=True, force_terminal=False, width=120)


def _runtime_event() -> RuntimeEvent:
    return RuntimeEvent(type="model_plan_start", message="Building model plan.", payload={"turn": 1})


def test_command_console_handles_status_and_repair_toggle(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.handle_input("/repair on")
    assert command_console.auto_repair is True
    assert command_console.handle_input("/status")

    output = console.export_text()
    assert "auto_repair: true" in output
    assert "Console Status" in output


def test_command_console_live_toggle_and_status(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console, live_enabled=False)

    assert command_console.handle_input("/live on")
    assert command_console.live_enabled is True
    assert command_console.handle_input("/status")
    assert command_console.handle_input("/live off")

    output = console.export_text()
    assert "live: true" in output
    assert "live" in output
    assert "live: false" in output


def test_command_console_cancel_sets_state(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)
    command_console.state.status = "running_tool"

    assert command_console.handle_input("/cancel")

    assert command_console.cancel_requested is True
    assert command_console.state.status == "cancelling"
    output = console.export_text()
    assert "Cancel requested" in output


def test_command_console_cancel_without_running_task_is_noop(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.handle_input("/cancel")

    assert command_console.cancel_requested is False
    assert "No running task to cancel" in console.export_text()


def test_command_console_refreshes_live_dashboard_on_events(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    command_console = CommandConsole(tmp_path, console=_console(), live_enabled=True)

    class FakeDashboard:
        refreshed = 0
        profile = ""
        auto_repair = False
        assume_yes = False

        def update_options(self, *, profile: str, auto_repair: bool, assume_yes: bool) -> None:
            self.profile = profile
            self.auto_repair = auto_repair
            self.assume_yes = assume_yes

        def refresh(self) -> None:
            self.refreshed += 1

    dashboard = FakeDashboard()
    command_console.live_dashboard = dashboard  # type: ignore[assignment]

    command_console.handle_event(command_console.state.events[0] if command_console.state.events else _runtime_event())

    assert dashboard.refreshed == 1
    assert dashboard.profile == "mock"


def test_command_console_runs_task_and_keeps_last_result(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)
    command_console.assume_yes = True

    assert command_console.handle_input("analyze this repo")

    assert command_console.last_result is not None
    assert command_console.last_result.status == "success"
    assert command_console.state.run_id == command_console.last_result.run_id
    assert command_console.state.plan_summary
    assert command_console.state.verification == "skipped_no_changes"
    output = console.export_text()
    assert "Run Result" in output
    assert "run_start" in output
    assert command_console.events


def test_command_console_builds_follow_up_task_from_last_result(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.build_runtime_task("first task") == "first task"
    assert command_console.handle_input("analyze this repo")

    follow_up = command_console.build_runtime_task("now verify the docs")

    assert "Follow-up task in the same console session." in follow_up
    assert "User request:\nnow verify the docs" in follow_up
    assert f"- run_id: {command_console.last_result.run_id}" in follow_up  # type: ignore[union-attr]
    assert f"- verification: {command_console.last_result.verification}" in follow_up  # type: ignore[union-attr]
    assert f"- summary: {command_console.last_result.summary_path}" in follow_up  # type: ignore[union-attr]


def test_command_console_state_commands_render_current_run(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.handle_input("analyze this repo")
    assert command_console.handle_input("/plan")
    assert command_console.handle_input("/context")
    assert command_console.handle_input("/evidence")
    assert command_console.handle_input("/verify")

    output = console.export_text()
    assert "Current Plan" in output
    assert "Context Summary" in output
    assert "Evidence Summary" in output
    assert "Verification" in output


def test_command_console_verify_runs_manual_verification(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    RuntimeApp(tmp_path).init_project()
    terminal_result = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=0,
        summary="passed",
    )
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: terminal_result)
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)
    command_console.assume_yes = True
    command_console.state.changed_files = ["demo.py"]

    assert command_console.handle_input("/verify")

    assert command_console.last_manual_verification is not None
    assert command_console.last_manual_verification.status == "passed"
    output = console.export_text()
    assert "Manual Verification Result" in output
    assert "python -m pytest" in output


def test_command_console_repair_runs_manual_repair(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    RuntimeApp(tmp_path).init_project()
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(
                    name="real",
                    provider="openai-compatible",
                    base_url="https://api.example.com/v1",
                    api_key_env="XHX_TEST_API_KEY",
                    model="demo-model",
                    stream=False,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    terminal_result = TerminalResult(
        command="python -m pytest",
        status="success",
        policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
        exit_code=0,
        summary="passed",
    )
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: terminal_result)
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)
    command_console.profile_name = "real"
    command_console.assume_yes = True
    command_console.runtime._build_plan = lambda _task, _profile, _context: ModelPlan(  # type: ignore[method-assign]
        summary="repair demo",
        steps=[
            ToolStep(
                tool="apply_patch",
                arguments={
                    "patch": """*** Begin Patch
*** Update File: demo.py
@@
-value = 1
+value = 2
*** End Patch
"""
                },
            )
        ],
    )
    command_console.last_manual_verification = command_console.runtime.verify_changed_files(
        ["demo.py"],
        assume_yes=True,
    )
    command_console.last_manual_verification.status = "failed"
    command_console.last_manual_verification.verification_results = [
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="expected value 2",
        )
    ]

    assert command_console.handle_input("/repair")

    assert command_console.last_manual_repair is not None
    assert command_console.last_manual_repair.verification == "passed"
    output = console.export_text()
    assert "Manual Repair Result" in output
    assert "python -m pytest" in output


def test_command_console_repair_loop_uses_two_attempts(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    RuntimeApp(tmp_path).init_project()
    profiles_path(tmp_path).write_text(
        ProfilesFile(
            profiles=[
                ModelProfile(
                    name="real",
                    provider="openai-compatible",
                    base_url="https://api.example.com/v1",
                    api_key_env="XHX_TEST_API_KEY",
                    model="demo-model",
                    stream=False,
                )
            ]
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    values = [2, 3]

    def fake_build_plan(_task, _profile, _context):
        next_value = values.pop(0)
        return ModelPlan(
            summary=f"set value to {next_value}",
            steps=[
                ToolStep(
                    tool="apply_patch",
                    arguments={
                        "patch": f"""*** Begin Patch
*** Update File: demo.py
@@
-value = {next_value - 1}
+value = {next_value}
*** End Patch
"""
                    },
                )
            ],
        )

    verification_results = [
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="initial failed verification",
        ),
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="expected value 3",
        ),
        TerminalResult(
            command="python -m pytest",
            status="success",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=0,
            summary="passed",
        ),
    ]
    monkeypatch.setattr("xhx_agent.safety.kernel.run_terminal", lambda *_args, **_kwargs: verification_results.pop(0))
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)
    command_console.profile_name = "real"
    command_console.assume_yes = True
    command_console.runtime._build_plan = fake_build_plan  # type: ignore[method-assign]
    command_console.last_manual_verification = command_console.runtime.verify_changed_files(["demo.py"], assume_yes=True)
    command_console.last_manual_verification.status = "failed"
    command_console.last_manual_verification.verification_results = [
        TerminalResult(
            command="python -m pytest",
            status="failed",
            policy=PolicyDecision(decision="allow", risk=RiskLevel.CONFIRM, reason="Command allowed by policy."),
            exit_code=1,
            summary="expected value 3",
        )
    ]

    assert command_console.handle_input("/repair loop")

    assert command_console.last_manual_repair is not None
    assert command_console.last_manual_repair.repair_attempts == 2
    assert command_console.last_manual_repair.verification == "passed"
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 3\n"


def test_command_console_plan_preview(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.handle_input("/plan analyze this repo")

    output = console.export_text()
    assert "Plan Preview" in output
    assert "trace" in output


def test_command_console_dashboard_renders_sections(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    console = _console()
    command_console = CommandConsole(tmp_path, console=console)

    assert command_console.handle_input("/dashboard")

    output = console.export_text()
    assert "Conversation" in output
    assert "Runtime State" in output
    assert "Context" in output
    assert "Commands" in output
    assert "/dashboard" in output


def test_command_console_exit_returns_false(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    command_console = CommandConsole(tmp_path, console=_console())

    assert command_console.handle_input("/exit") is False
