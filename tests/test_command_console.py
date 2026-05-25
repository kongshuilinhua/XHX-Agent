from pathlib import Path

from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel


def _console() -> Console:
    return Console(record=True, force_terminal=False, width=120)


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
