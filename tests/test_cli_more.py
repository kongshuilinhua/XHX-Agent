"""cli/main.py 补充冒烟：init / config / memory / compact 等命令。"""

from __future__ import annotations

from pathlib import Path

import click.testing
from typer.testing import CliRunner

from xhx_agent.cli.main import app

runner = CliRunner()
runner.isolated_filesystem = click.testing.CliRunner().isolated_filesystem


def test_init_project() -> None:
    with runner.isolated_filesystem() as ws:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        assert "Initialized xhx-agent project" in result.output
        assert (Path(ws) / ".xhx").exists()


def test_config_list() -> None:
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = runner.invoke(app, ["config", "list"])
        assert result.exit_code == 0, result.output
        assert "config:" in result.output and "profiles:" in result.output


def test_config_set_profile() -> None:
    result = runner.invoke(app, ["config", "set-profile", "myprofile"])
    assert result.exit_code == 0
    assert "myprofile" in result.output


def test_memory_empty() -> None:
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = runner.invoke(app, ["memory"])
        assert result.exit_code == 0, result.output
        assert "No memories" in result.output


def test_compact_without_session() -> None:
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = runner.invoke(app, ["compact"])
        assert result.exit_code == 0, result.output
        assert "No recent session transcript" in result.output


def test_sessions_empty() -> None:
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = runner.invoke(app, ["sessions"])
        assert result.exit_code == 0
        assert "No sessions recorded" in result.output
