from xhx_agent.memory.extract import parse_memory_candidates, propose_memories
from xhx_agent.models.mock import MockModelClient


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
        "MEMORY | type=project | name=already-existing | desc=desc\nMEMORY | type=project | name=new-one | desc=desc\n"
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
