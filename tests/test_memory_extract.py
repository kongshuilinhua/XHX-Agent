from rich.console import Console

from xhx_agent.cli.completion import XhxCompleter
from xhx_agent.cli.console import CommandConsole
from xhx_agent.memory.extract import parse_memory_candidates, propose_memories
from xhx_agent.memory.store import MemoryRecord
from xhx_agent.models.mock import MockModelClient
from xhx_agent.runtime.app import RunResult, RuntimeApp


def test_automem_switch_and_autocomplete(tmp_path):
    # 1. Autocomplete check
    completer = XhxCompleter(tmp_path)
    res = completer.get_completions("/a")
    assert "/automem" in res

    # 2. Console check
    RuntimeApp(tmp_path).init_project()
    console = Console(record=True, force_terminal=False, width=120)
    cc = CommandConsole(tmp_path, console=console)

    # Initial state should be True
    assert cc.auto_memory is True

    # Turn off
    assert cc.handle_input("/automem off")
    assert cc.auto_memory is False
    output = console.export_text()
    assert "auto_memory: false" in output

    # Turn on
    assert cc.handle_input("/automem on")
    assert cc.auto_memory is True
    output = console.export_text()
    assert "auto_memory: true" in output

    # Show status
    assert cc.handle_input("/automem")
    output = console.export_text()
    assert "auto_memory: true" in output


def test_checkpoint_1_strict_parsing():
    # A) Valid lines
    text = (
        "MEMORY | type=project | name=test-project | desc=Testing description | body=Testing body\n"
        "MEMORY | type=feedback | name=test-feedback | desc=Feedback description | body=Feedback body\n"
    )
    candidates = parse_memory_candidates(text)
    assert len(candidates) == 2
    assert candidates[0].mtype == "project"
    assert candidates[0].name == "test-project"
    assert candidates[0].description == "Testing description"
    assert candidates[0].body == "Testing body"
    assert candidates[1].mtype == "feedback"
    assert candidates[1].name == "test-feedback"
    assert candidates[1].description == "Feedback description"
    assert candidates[1].body == "Feedback body"

    # B) NONE / empty / unrelated
    assert parse_memory_candidates("NONE") == []
    assert parse_memory_candidates("") == []
    assert parse_memory_candidates("Mock loop reply: bla bla") == []

    # C) Invalid type or missing name
    text_invalid = (
        "MEMORY | type=invalid_type | name=test | desc=desc | body=body\n"
        "MEMORY | type=project | desc=missing name | body=body\n"
    )
    assert parse_memory_candidates(text_invalid) == []


def test_checkpoint_2_body_tolerance():
    text = "MEMORY | type=project | name=test-body | desc=desc | body=This body contains | pipes | and = equal signs"
    candidates = parse_memory_candidates(text)
    assert len(candidates) == 1
    assert candidates[0].body == "This body contains | pipes | and = equal signs"


def test_checkpoint_3_deduplication_and_limit():
    # Existing names check (slugified match)
    text = (
        "MEMORY | type=project | name=already-existing | desc=desc\n"
        "MEMORY | type=project | name=new-one | desc=desc\n"
    )
    candidates = parse_memory_candidates(text, existing_names={"already-existing"})
    assert len(candidates) == 1
    assert candidates[0].name == "new-one"

    # Within same candidates list deduplication (dup-test and dup test both resolve to dup-test)
    text_dup = (
        "MEMORY | type=project | name=dup-test | desc=desc1\n"
        "MEMORY | type=project | name=dup-test | desc=desc2\n"
        "MEMORY | type=project | name=dup test | desc=desc3\n"
    )
    candidates_dup = parse_memory_candidates(text_dup)
    assert len(candidates_dup) == 1
    assert candidates_dup[0].name == "dup-test"

    # Limit check
    text_limit = (
        "MEMORY | type=project | name=one | desc=desc\n"
        "MEMORY | type=project | name=two | desc=desc\n"
        "MEMORY | type=project | name=three | desc=desc\n"
    )
    candidates_limit = parse_memory_candidates(text_limit, limit=2)
    assert len(candidates_limit) == 2
    assert [c.name for c in candidates_limit] == ["one", "two"]


class MockResult:
    def __init__(self, content):
        self.content = content


class FakeChatClient:
    def __init__(self, content):
        self.res = MockResult(content)

    def chat(self, messages, tools):
        return self.res


def test_checkpoint_4_propose_fake_client():
    # Valid output from model client
    client = FakeChatClient("MEMORY | type=project | name=propose-test | desc=Testing description | body=Testing body")
    candidates = propose_memories(client, "task text", "transcript text")
    assert len(candidates) == 1
    assert candidates[0].name == "propose-test"

    # Unformatted output
    client_unformatted = FakeChatClient("unformatted chat output")
    candidates_unf = propose_memories(client_unformatted, "task text", "transcript text")
    assert candidates_unf == []


def test_checkpoint_5_mock_determinism():
    client = MockModelClient()
    candidates = propose_memories(client, "task text", "transcript text")
    assert candidates == []


def test_checkpoint_6_suggest_confirm_integration(tmp_path, monkeypatch):
    import xhx_agent.memory
    from xhx_agent.memory.store import list_memories

    RuntimeApp(tmp_path).init_project()
    console = Console(record=True, force_terminal=False, width=120)
    cc = CommandConsole(tmp_path, console=console)

    # Sub-case A: confirm -> write
    record_yes = MemoryRecord(name="integration-yes", description="desc yes", mtype="project", body="body yes")
    monkeypatch.setattr(xhx_agent.memory, "propose_memories", lambda *args, **kwargs: [record_yes])
    monkeypatch.setattr(cc, "_confirm_memory", lambda record: True)

    res_success = RunResult(
        run_id="r1",
        status="success",
        changed_files=[],
        commands=[],
        verification="not_executed",
        summary_path="p",
        risk_summary=[],
        mode="loop",
    )
    cc._maybe_suggest_memories(res_success)

    mems = list_memories(tmp_path)
    assert len(mems) == 1
    assert mems[0].name == "integration-yes"

    # Sub-case B: reject -> do not write
    record_no = MemoryRecord(name="integration-no", description="desc no", mtype="project", body="body no")
    monkeypatch.setattr(xhx_agent.memory, "propose_memories", lambda *args, **kwargs: [record_no])
    monkeypatch.setattr(cc, "_confirm_memory", lambda record: False)

    cc._maybe_suggest_memories(res_success)
    mems = list_memories(tmp_path)
    # only the first one should be there
    assert len(mems) == 1
    assert mems[0].name == "integration-yes"

    # Sub-case C: auto_memory is False -> do not extract
    cc.auto_memory = False
    called = False
    def fake_propose(*args, **kwargs):
        nonlocal called
        called = True
        return []
    monkeypatch.setattr(xhx_agent.memory, "propose_memories", fake_propose)

    cc._maybe_suggest_memories(res_success)
    assert not called

    # Sub-case D: status != "success" -> do not extract
    cc.auto_memory = True
    res_failed = RunResult(
        run_id="r2",
        status="failed",
        changed_files=[],
        commands=[],
        verification="failed",
        summary_path="p",
        risk_summary=[],
        mode="loop",
    )
    cc._maybe_suggest_memories(res_failed)
    assert not called
