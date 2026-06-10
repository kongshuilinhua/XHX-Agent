import json
from xhx_agent.models.openai_compatible import OpenAICompatibleClient

class _FakeResp:
    status_code = 200
    def __init__(self, payload): self._p = payload
    def json(self): return self._p

class _FakeHTTP:
    def __init__(self, payload): self._p = payload; self.last = None
    def post(self, url, headers=None, json=None): self.last = json; return _FakeResp(self._p)

def _client(payload, monkeypatch):
    monkeypatch.setenv("XHX_TEST_KEY", "sk-test")
    return OpenAICompatibleClient(base_url="http://x/v1", api_key_env="XHX_TEST_KEY",
                                  model="m", http_client=_FakeHTTP(payload))

def test_chat_returns_text(monkeypatch):
    payload = {"choices": [{"message": {"content": "hi there", "tool_calls": None}}]}
    res = _client(payload, monkeypatch).chat([{"role": "user", "content": "hi"}], tools=[])
    assert res.content == "hi there"
    assert res.tool_calls == []

def test_chat_parses_tool_calls(monkeypatch):
    payload = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "call_1", "type": "function",
         "function": {"name": "read_file", "arguments": json.dumps({"path": "a.py"})}}]}}]}
    res = _client(payload, monkeypatch).chat([{"role": "user", "content": "read a.py"}], tools=[])
    assert len(res.tool_calls) == 1
    tc = res.tool_calls[0]
    assert (tc.id, tc.name, tc.arguments) == ("call_1", "read_file", {"path": "a.py"})

from xhx_agent.models import build_chat_client
from xhx_agent.runtime.profiles import ModelProfile

def test_mock_chat_question_returns_text():
    client = build_chat_client(ModelProfile(name="mock", provider="mock", base_url="", api_key_env="", model="mock"))
    res = client.chat([{"role": "user", "content": "你是谁"}], tools=[])
    assert res.content and not res.tool_calls

def test_mock_chat_edit_then_done():
    client = build_chat_client(ModelProfile(name="mock", provider="mock", base_url="", api_key_env="", model="mock"))
    msgs = [{"role": "user", "content": "fix the bug in a.py"}]
    r1 = client.chat(msgs, tools=[])
    assert r1.tool_calls and r1.tool_calls[0].name == "read_file"
    msgs += [{"role": "tool", "tool_call_id": r1.tool_calls[0].id, "content": "file content"}]
    r2 = client.chat(msgs, tools=[])
    assert r2.content and not r2.tool_calls
