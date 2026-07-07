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


def test_record_run_session_passes_through_result_fields() -> None:
    # headless 结果里的 verification/changed_files/turns 必须落入会话索引，
    # 否则 `xhx sessions` 对 headless 运行永远显示不出这些真实值。
    from xhx_agent.cli.main import _record_run_session
    from xhx_agent.runtime.headless import HeadlessResult
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = HeadlessResult(
            status="completed",
            summary="done",
            verification="passed",
            turns=3,
            changed_files=["a.py", "b.py"],
        )
        entry = _record_run_session(Path(ws), "some task", result)
        assert entry.verification == "passed"
        assert entry.changed_files == ["a.py", "b.py"]
        assert entry.turn_count == 3
        assert (Path(ws) / entry.summary_path).read_text(encoding="utf-8") == "done"


def test_record_run_session_persists_transcript_and_restores() -> None:
    # run_id 与 trace/会话索引共用；messages 落盘 transcript，_restore_conversation 能全量还原。
    from xhx_agent.cli.main import _record_run_session, _restore_conversation
    from xhx_agent.runtime.headless import HeadlessResult
    from xhx_agent.runtime.init import init_project

    with runner.isolated_filesystem() as ws:
        init_project(Path(ws))
        result = HeadlessResult(
            status="completed",
            summary="hi there",
            run_id="abc123def456",
            messages=[
                {"type": "message", "role": "user", "content": "say hi"},
                {"type": "message", "role": "assistant", "content": "hi there"},
            ],
        )
        entry = _record_run_session(Path(ws), "say hi", result)

        assert entry.run_id == "abc123def456"
        assert entry.transcript_path
        assert (Path(ws) / entry.transcript_path).exists()

        conversation = _restore_conversation(Path(ws), entry)
        assert conversation is not None
        history = conversation.get_messages()
        assert [m.role for m in history] == ["user", "assistant"]
        assert history[-1].content == "hi there"
        assert conversation.env_injected and conversation.ltm_injected
