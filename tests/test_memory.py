from pathlib import Path

import pytest

from xhx_agent.memory.store import (
    list_memories,
    memory_dir,
    parse_memory_file,
    write_memory,
)


def test_store_round_trip(tmp_path):
    # 1. write memory and retrieve it
    workspace = tmp_path
    name = "Test Memory"
    description = "This is a test memory"
    mtype = "project"
    body = "Test body content"

    path = write_memory(workspace, name=name, description=description, mtype=mtype, body=body)
    assert path.exists()

    memories = list_memories(workspace)
    assert len(memories) == 1
    record = memories[0]
    assert record.name == name
    assert record.description == description
    assert record.mtype == mtype
    assert record.body == body
    assert record.path == path

    # 2. Parse direct memory file
    parsed = parse_memory_file(path)
    assert parsed is not None
    assert parsed.name == name
    assert parsed.description == description
    assert parsed.mtype == mtype
    assert parsed.body == body

    # 3. Check MEMORY.md index line
    index_file = memory_dir(workspace) / "MEMORY.md"
    assert index_file.exists()
    index_content = index_file.read_text(encoding="utf-8")
    assert f"- [{name}](test-memory.md) — {description}" in index_content

    # 4. Overwrite same name
    new_description = "Updated description"
    new_body = "Updated body"
    new_path = write_memory(workspace, name=name, description=new_description, mtype=mtype, body=new_body)
    assert new_path == path

    memories = list_memories(workspace)
    assert len(memories) == 1
    assert memories[0].description == new_description
    assert memories[0].body == new_body

    # Check index updated
    index_content = index_file.read_text(encoding="utf-8")
    assert f"- [{name}](test-memory.md) — {new_description}" in index_content
    assert f"- [{name}](test-memory.md) — {description}" not in index_content

    # 5. Invalid type should raise ValueError
    with pytest.raises(ValueError):
        write_memory(workspace, name="another", description="desc", mtype="invalid_type", body="body")


def test_recall_determinism_and_ranking(tmp_path):
    from xhx_agent.memory.recall import recall_memories

    workspace = tmp_path

    # Write 3 memory records
    write_memory(workspace, name="uv-command", description="Use uv for dependency management", mtype="project", body="uv run pytest is used for running tests.")
    write_memory(workspace, name="python-version", description="Project python interpreter", mtype="project", body="We run python 3.11. dependency is important.")
    write_memory(workspace, name="ignored-memory", description="random text data", mtype="project", body="lorem ipsum dolor sit amet.")

    # 1. Determinism
    q = "dependency uv run"
    res1 = recall_memories(workspace, q)
    for _ in range(5):
        assert recall_memories(workspace, q) == res1

    # 2. Ranking: "uv-command" should be first because it matches "dependency" strongly (in description) and "uv"/"run"
    # "python-version" matches "dependency" weakly (in body)
    # "ignored-memory" has no match and shouldn't even be returned
    assert len(res1) == 2
    assert res1[0].name == "uv-command"
    assert res1[1].name == "python-version"

    # 3. Limit
    res_limit = recall_memories(workspace, q, limit=1)
    assert len(res_limit) == 1
    assert res_limit[0].name == "uv-command"

    # 4. No hit returns []
    res_empty = recall_memories(workspace, "completelyunrelatedquery")
    assert res_empty == []


def test_lifecycle_verification(tmp_path):
    from xhx_agent.memory.recall import recall_memories

    workspace = tmp_path

    # Write memories referencing files
    write_memory(workspace, name="existent-file", description="refers to an existent file", mtype="project", body="referring to `src/existing.py` in code.")
    write_memory(workspace, name="nonexistent-file", description="refers to a nonexistent file", mtype="project", body="referring to `src/missing.py` in code.")

    # Create the existent file (make parent dir first)
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "existing.py").write_text("# exist", encoding="utf-8")

    # 1. With verify=True (default), nonexistent-file should be skipped
    recalled_verify = recall_memories(workspace, "referring", verify=True)
    assert len(recalled_verify) == 1
    assert recalled_verify[0].name == "existent-file"

    # 2. With verify=False, both should be recalled
    recalled_no_verify = recall_memories(workspace, "referring", verify=False)
    assert len(recalled_no_verify) == 2
    names = {m.name for m in recalled_no_verify}
    assert names == {"existent-file", "nonexistent-file"}


def test_compiler_injection(tmp_path):
    from xhx_agent.context.compiler import compile_context_pack
    from xhx_agent.memory.store import write_memory
    from xhx_agent.repo_intel.scanner import scan_project

    workspace = tmp_path
    scan = scan_project(workspace)

    # 1. Empty memory dir: no memory kind context items should be present
    pack_empty = compile_context_pack(workspace=workspace, task="some task about testing", scan=scan)
    memory_items_empty = [item for item in pack_empty.items if item.kind.startswith("memory")]
    assert len(memory_items_empty) == 0

    # 2. Write a relevant memory
    write_memory(workspace, name="testing-setup", description="We use pytest for testing.", mtype="project", body="Run tests with pytest.")

    # Run with a query matching the memory (e.g. task matches 'testing')
    pack_match = compile_context_pack(workspace=workspace, task="How do we do testing?", scan=scan)
    memory_items_match = [item for item in pack_match.items if item.kind.startswith("memory")]
    assert len(memory_items_match) == 1
    assert memory_items_match[0].source == "testing-setup"
    assert "pytest" in memory_items_match[0].content


def test_orchestrator_injection(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    from xhx_agent.memory.store import write_memory
    from xhx_agent.models.types import ChatResult
    from xhx_agent.runtime.app import RuntimeApp


    (tmp_path / "README.md").write_text("# demo\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()

    captured_messages = []

    class FakeClient:
        def chat(self, messages, tools):
            captured_messages.extend(messages)
            return ChatResult(content="Done task in loop", tool_calls=[])

    monkeypatch.setattr(loopmod, "build_chat_client", lambda profile: FakeClient())

    # Case 1: Memory is empty
    res1 = app.run_task("verify python", profile_name="mock", mode="loop")
    assert res1.status == "success"
    assert len(captured_messages) > 0
    system_msg1 = captured_messages[0]["content"]
    assert "Recalled memory" not in system_msg1

    captured_messages.clear()

    # Case 2: Relevant memory exists
    write_memory(tmp_path, name="python-run", description="We run python", mtype="project", body="Use python version 3.11 always.")
    res2 = app.run_task("verify python", profile_name="mock", mode="loop")
    assert res2.status == "success"
    assert len(captured_messages) > 0
    system_msg2 = captured_messages[0]["content"]
    assert "Recalled memory (cross-session facts)" in system_msg2
    assert "Use python version 3.11 always." in system_msg2


def test_repl_slash_commands_and_autocomplete(tmp_path):
    # 1. Autocomplete check
    from xhx_agent.cli.completion import XhxCompleter
    completer = XhxCompleter(tmp_path)
    res = completer.get_completions("/")
    assert "/remember" in res
    assert "/memory" in res

    # 2. Console check
    from rich.console import Console

    from xhx_agent.cli.console import CommandConsole
    from xhx_agent.runtime.app import RuntimeApp

    RuntimeApp(tmp_path).init_project()
    console = Console(record=True, force_terminal=False, width=120)
    command_console = CommandConsole(tmp_path, console=console)

    # Run /remember
    assert command_console.handle_input("/remember This is a console memory test.")

    # Verify file created
    from xhx_agent.memory.store import list_memories
    mems = list_memories(tmp_path)
    assert len(mems) == 1
    assert mems[0].body == "This is a console memory test."

    # Run /memory
    assert command_console.handle_input("/memory")
    output = console.export_text()
    assert "This is a console memory test." in output


def test_cli_subcommand(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from xhx_agent.cli.main import app
    from xhx_agent.memory.store import write_memory

    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    runner = CliRunner()

    # 1. Run memory command when empty
    result = runner.invoke(app, ["memory"])
    assert result.exit_code == 0
    assert "No memories recorded yet." in result.output

    # 2. Write memory
    write_memory(tmp_path, name="command-test", description="Test CLI subcommand", mtype="project", body="Body of command test.")

    # 3. Run memory command again
    result2 = runner.invoke(app, ["memory"])
    assert result2.exit_code == 0
    assert "command-test" in result2.output
    assert "Test CLI subcommand" in result2.output










