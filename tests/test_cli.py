from pathlib import Path

from xhx_agent.cli.main import _confirm_terminal_command
from xhx_agent.cli.main import app
from xhx_agent.repo_intel.index import write_repo_intel_index
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


def test_repo_index_command_reports_missing_index() -> None:
    with runner.isolated_filesystem():
        result = runner.invoke(app, ["repo-index"])

    assert result.exit_code == 0
    assert "repo index: missing" in result.output


def test_repo_index_command_json_reports_current_index() -> None:
    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        (root / "src").mkdir()
        (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        write_repo_intel_index(root)

        result = runner.invoke(app, ["repo-index", "--json"])

    assert result.exit_code == 0
    assert '"status": "current"' in result.output
    assert '"symbol_count": 1' in result.output
