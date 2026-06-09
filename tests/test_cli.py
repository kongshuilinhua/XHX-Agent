import re
from pathlib import Path

from typer.testing import CliRunner

from xhx_agent.cli.main import _confirm_terminal_command, app
from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.risk import RiskLevel

runner = CliRunner()


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mK]", "", text)


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
    assert "--fullscreen" in strip_ansi(result.output)


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
    assert '"call_edge_count": 0' in result.output


def test_repo_index_refresh_creates_missing_index() -> None:
    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        (root / "src").mkdir()
        (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        result = runner.invoke(app, ["repo-index", "--refresh"])

        assert (root / ".xhx" / "repo" / "index.json").exists()

    assert result.exit_code == 0
    assert "repo index: current" in result.output


def test_repo_index_refresh_json_reports_current_index() -> None:
    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        (root / "src").mkdir()
        (root / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        result = runner.invoke(app, ["repo-index", "--refresh", "--json"])

    assert result.exit_code == 0
    assert '"status": "current"' in result.output
    assert '"symbol_count": 1' in result.output


def test_run_continue_records_and_resumes_session() -> None:
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.runtime.session import session_history_path

    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        RuntimeApp(root).init_project()

        first = runner.invoke(app, ["run", "analyze the repo", "--profile", "mock"])
        assert first.exit_code == 0, first.output
        assert session_history_path(root).exists()

        second = runner.invoke(app, ["run", "keep going", "--profile", "mock", "--continue"])
        assert second.exit_code == 0, second.output
        assert "Continuing from run" in strip_ansi(second.output)

        lines = [line for line in session_history_path(root).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2


def test_sessions_command_and_resume_by_id() -> None:
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.runtime.session import list_sessions

    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        RuntimeApp(root).init_project()

        empty = runner.invoke(app, ["sessions"])
        assert empty.exit_code == 0
        assert "No sessions recorded" in empty.output

        first = runner.invoke(app, ["run", "analyze the repo", "--profile", "mock"])
        assert first.exit_code == 0, first.output

        listed = runner.invoke(app, ["sessions"])
        assert listed.exit_code == 0
        assert "Sessions" in strip_ansi(listed.output)

        run_id = list_sessions(root)[-1].run_id
        resumed = runner.invoke(app, ["run", "keep going", "--profile", "mock", "--resume", run_id])
        assert resumed.exit_code == 0, resumed.output
        assert "Resuming from run" in strip_ansi(resumed.output)


def test_run_mode_flag_accepted() -> None:
    from xhx_agent.runtime.app import RuntimeApp

    with runner.isolated_filesystem() as workspace:
        root = Path(workspace)
        RuntimeApp(root).init_project()
        result = runner.invoke(app, ["run", "analyze the repo", "--profile", "mock", "--mode", "loop"])
        assert result.exit_code == 0, result.output
        assert "status:" in result.output
