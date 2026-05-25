from pathlib import Path

from xhx_agent.tools.terminal import run_terminal


def test_terminal_confirm_without_callback_does_not_execute(tmp_path: Path) -> None:
    result = run_terminal(tmp_path, "python -m pytest")

    assert result.status == "confirm"
    assert result.exit_code is None
    assert "requires user confirmation" in result.summary.lower()


def test_terminal_callback_accepts_confirm_command(tmp_path: Path) -> None:
    result = run_terminal(
        tmp_path,
        "python -c \"print('ok')\"",
        confirm_callback=lambda _command, _decision: True,
    )

    assert result.status == "success"
    assert result.exit_code == 0
    assert "ok" in result.summary


def test_terminal_callback_declines_confirm_command(tmp_path: Path) -> None:
    result = run_terminal(
        tmp_path,
        "python -c \"print('ok')\"",
        confirm_callback=lambda _command, _decision: False,
    )

    assert result.status == "confirm"
    assert result.exit_code is None
    assert result.summary == "User declined command confirmation."


def test_terminal_summary_is_truncated(tmp_path: Path) -> None:
    result = run_terminal(
        tmp_path,
        "python -c \"print('x' * 5000)\"",
        assume_yes=True,
    )

    assert result.status == "success"
    assert len(result.summary) < 4100
    assert result.summary.startswith("...<truncated>")
