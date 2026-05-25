from pathlib import Path

from rich.console import Console

from xhx_agent.cli.console import CommandConsole
from xhx_agent.runtime.app import RuntimeApp


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
    output = console.export_text()
    assert "Run Result" in output
    assert "run_start" in output
    assert command_console.events


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
    assert "Console Status" in output
    assert "Last Run" in output
    assert "Recent Events" in output
    assert "/dashboard" in output


def test_command_console_exit_returns_false(tmp_path: Path) -> None:
    RuntimeApp(tmp_path).init_project()
    command_console = CommandConsole(tmp_path, console=_console())

    assert command_console.handle_input("/exit") is False
