from xhx_agent.models.mock import MockModelClient
from xhx_agent.orchestrators.compaction import compact_messages
from xhx_agent.runtime.app import RuntimeApp


class CallCounterSummarize:
    def __init__(self, return_val="summary"):
        self.calls = 0
        self.return_val = return_val

    def summarize(self, text: str) -> str:
        self.calls += 1
        return self.return_val


def test_compaction_no_op_when_below_threshold():
    counter = CallCounterSummarize()
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "hello"},
    ]
    res = compact_messages(messages, counter.summarize, max_tokens=10000)
    assert res == messages
    assert counter.calls == 0


def test_compaction_effectiveness():
    counter = CallCounterSummarize("This is compacted text")
    # Setup messages that are large
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "very long message " * 10},
        {"role": "assistant", "content": "response " * 10},
        {"role": "user", "content": "another long message " * 10},
        {"role": "assistant", "content": "ok", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "user", "content": "tail"},
    ]

    # We set max_tokens to 10 so it's guaranteed to compress
    res = compact_messages(messages, counter.summarize, max_tokens=10, keep_recent=2)
    assert counter.calls == 1

    # First is system
    assert res[0]["role"] == "system"
    # Second is compaction summary message
    assert res[1]["role"] == "user"
    assert "[Earlier turns compacted to save context]" in res[1]["content"]
    assert "This is compacted text" in res[1]["content"]

    # Check that tail does not start with tool (non-tool tail adjustment)
    # The original messages: assistant(c1) -> tool(c1) -> user(tail)
    # If keep_recent = 2, body cut starts from assistant(c1). But assistant(c1) is assistant message (not tool).
    # Wait, in compacted, the kept recent messages are: assistant(c1), tool(c1), user(tail).
    # Since assistant(c1) is not "tool", it's fine.
    # What if cut landed on tool(c1)? It shifts to right, so it would keep only user(tail).
    # Let's verify no orphaned tool message is in the result.
    tool_call_ids = set()
    for msg in res:
        if "tool_calls" in msg:
            for tc in msg["tool_calls"]:
                tool_call_ids.add(tc["id"])
        if msg.get("role") == "tool":
            # Must have corresponding preceding tool_call id
            assert msg.get("tool_call_id") in tool_call_ids


def test_compaction_keep_recent_ge_body():
    counter = CallCounterSummarize()
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "long message " * 10},
        {"role": "assistant", "content": "ok"},
    ]
    # keep_recent is 10, which is >= body length (2). Should not compact.
    res = compact_messages(messages, counter.summarize, max_tokens=10, keep_recent=10)
    assert res == messages
    assert counter.calls == 0


def test_loop_integration_compaction(tmp_path, monkeypatch):
    import xhx_agent.orchestrators.loop as loopmod
    RuntimeApp(tmp_path).init_project()

    # Monkeypatch compact_messages to track it
    calls = []
    original_compact = loopmod.compact_messages
    def mock_compact(messages, summarize_fn, **kwargs):
        calls.append(messages)
        # Force a small threshold so it triggers compaction during the loop run
        return original_compact(messages, summarize_fn, max_tokens=10, keep_recent=2, **kwargs)

    monkeypatch.setattr(loopmod, "compact_messages", mock_compact)

    # Let's monkeypatch MockModelClient.chat to return several tool calls so the conversation grows
    turn_responses = [
        # Turn 1: calls tool
        {"content": "first response", "tool_calls": [{"id": "t1", "name": "read_file", "arguments": {"path": "README.md"}}]},
        # Turn 2: calls another tool
        {"content": "second response", "tool_calls": [{"id": "t2", "name": "read_file", "arguments": {"path": "README.md"}}]},
        # Turn 3: answers
        {"content": "all done", "tool_calls": []}
    ]

    original_chat = MockModelClient.chat
    def mock_chat(self, messages, tools):
        if turn_responses:
            r = turn_responses.pop(0)
            from xhx_agent.models.types import ChatResult, ToolCall
            return ChatResult(
                content=r["content"],
                tool_calls=[ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in r["tool_calls"]]
            )
        return original_chat(self, messages, tools)

    monkeypatch.setattr(MockModelClient, "chat", mock_chat)

    # Run the loop task
    res = RuntimeApp(tmp_path).run_task("fix bugs in python", profile_name="mock", mode="loop")
    assert res.status == "success"

    # Verify that compact_messages was called on subsequent turns
    assert len(calls) > 0
    # At least one call should have resulted in a reduced list of messages due to low max_tokens
    # (Checking if the final history contains the compaction prefix)
