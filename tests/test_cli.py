from xhx_agent.cli.main import _confirm_terminal_command
from xhx_agent.cli.main import app
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel
from typer.testing import CliRunner


runner = CliRunner()


def test_cli_confirmation_decline_returns_false(monkeypatch) -> None:
    monkeypatch.setattr("xhx_agent.cli.main.typer.confirm", lambda *_args, **_kwargs: False)
    decision = PolicyDecision(
        decision="confirm",
        risk=RiskLevel.CONFIRM,
        reason="Command requires user confirmation.",
        requires_user=True,
    )

    assert not _confirm_terminal_command("uv run pytest", decision)


def test_tui_help_exposes_fullscreen_option() -> None:
    result = runner.invoke(app, ["tui", "--help"])

    assert result.exit_code == 0
    assert "--fullscreen" in result.output
