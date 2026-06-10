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
