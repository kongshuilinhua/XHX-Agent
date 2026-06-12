import json

import pytest

from xhx_agent.models.mock import MockModelClient
from xhx_agent.models.routing import (
    FallbackChatClient,
    build_routed_client,
    resolve_profile_for_role,
)
from xhx_agent.models.types import ChatResult, ModelClientError, ToolCall
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.config import load_config


def test_checkpoint_1_config_backward_compatibility_and_parsing(tmp_path):
    # 1. Config without routing (backward compatibility)
    config_file = tmp_path / ".xhx" / "config.json"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "mock",
        "workspace_root": "."
    }), encoding="utf-8")

    cfg = load_config(tmp_path)
    assert cfg.routing is not None
    assert cfg.routing.roles == {}
    assert cfg.routing.fallback == []

    # 2. Config with explicit routing
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "mock",
        "workspace_root": ".",
        "routing": {
            "roles": {"explore": "cheap"},
            "fallback": ["mock"]
        }
    }), encoding="utf-8")

    cfg2 = load_config(tmp_path)
    assert cfg2.routing is not None
    assert cfg2.routing.roles == {"explore": "cheap"}
    assert cfg2.routing.fallback == ["mock"]


def test_checkpoint_2_resolve_profile_for_role(tmp_path):
    if resolve_profile_for_role is None:
        pytest.fail("resolve_profile_for_role is not implemented.")

    RuntimeApp(tmp_path).init_project()
    # Write custom profiles.json with 'base' and 'cheap'
    profiles_file = tmp_path / ".xhx" / "profiles.json"
    profiles_file.write_text(json.dumps({
        "profiles": [
            {
                "name": "base",
                "provider": "mock",
                "base_url": "",
                "api_key_env": "",
                "model": "base-mock"
            },
            {
                "name": "cheap",
                "provider": "mock",
                "base_url": "",
                "api_key_env": "",
                "model": "cheap-mock"
            }
        ]
    }), encoding="utf-8")

    # Write config.json mapping explore to cheap
    config_file = tmp_path / ".xhx" / "config.json"
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "base",
        "routing": {
            "roles": {"explore": "cheap"}
        }
    }), encoding="utf-8")

    # Test resolved profile matches 'cheap'
    profile = resolve_profile_for_role(tmp_path, "explore", "base")
    assert profile.name == "cheap"
    assert profile.model == "cheap-mock"

    # Test unmapped role resolved to 'base'
    profile_base = resolve_profile_for_role(tmp_path, "other_role", "base")
    assert profile_base.name == "base"
    assert profile_base.model == "base-mock"

    # Test missing mapped profile throws ValueError
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "base",
        "routing": {
            "roles": {"explore": "missing_profile"}
        }
    }), encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_profile_for_role(tmp_path, "explore", "base")


class DummyClient:
    def __init__(self, mode="success", result_val="ok"):
        self.mode = mode
        self.result_val = result_val
        self.calls = 0

    def chat(self, messages, tools):
        self.calls += 1
        if self.mode == "fail":
            raise ModelClientError(code="test_fail", message="mock failure")
        return ChatResult(content=self.result_val, tool_calls=[])


def test_checkpoint_3_fallback_chat_client():
    if FallbackChatClient is None:
        pytest.fail("FallbackChatClient is not implemented.")

    # Case A: primary succeeds
    c1 = DummyClient(mode="success", result_val="c1_ok")
    c2 = DummyClient(mode="success", result_val="c2_ok")
    fallback_events = []

    client = FallbackChatClient([c1, c2], on_fallback=lambda idx, err: fallback_events.append((idx, err)))
    res = client.chat([], [])

    assert res.content == "c1_ok"
    assert c1.calls == 1
    assert c2.calls == 0
    assert len(fallback_events) == 0

    # Case B: primary fails, secondary succeeds
    c1_fail = DummyClient(mode="fail")
    c2_ok = DummyClient(mode="success", result_val="c2_ok")
    client_b = FallbackChatClient([c1_fail, c2_ok], on_fallback=lambda idx, err: fallback_events.append((idx, err)))
    res_b = client_b.chat([], [])

    assert res_b.content == "c2_ok"
    assert c1_fail.calls == 1
    assert c2_ok.calls == 1
    assert len(fallback_events) == 1
    assert fallback_events[0][0] == 0
    assert isinstance(fallback_events[0][1], ModelClientError)

    # Case C: all fail
    c1_fail2 = DummyClient(mode="fail")
    c2_fail2 = DummyClient(mode="fail")
    client_c = FallbackChatClient([c1_fail2, c2_fail2], on_fallback=lambda idx, err: None)
    with pytest.raises(ModelClientError) as excinfo:
        client_c.chat([], [])
    assert excinfo.value.code == "test_fail"


def test_checkpoint_4_build_routed_client(tmp_path):
    if build_routed_client is None:
        pytest.fail("build_routed_client is not implemented.")

    RuntimeApp(tmp_path).init_project()
    # Setup base and cheap profiles
    profiles_file = tmp_path / ".xhx" / "profiles.json"
    profiles_file.write_text(json.dumps({
        "profiles": [
            {
                "name": "base",
                "provider": "mock",
                "model": "base-mock"
            },
            {
                "name": "cheap",
                "provider": "mock",
                "model": "cheap-mock"
            }
        ]
    }), encoding="utf-8")

    # Case A: no fallback configured
    config_file = tmp_path / ".xhx" / "config.json"
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "base",
        "routing": {
            "roles": {},
            "fallback": []
        }
    }), encoding="utf-8")

    client_a = build_routed_client(tmp_path, role="loop", base_profile_name="base")
    assert isinstance(client_a, MockModelClient)
    assert not isinstance(client_a, FallbackChatClient)

    # Case B: fallback configured
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "base",
        "routing": {
            "roles": {"explore": "cheap"},
            "fallback": ["base", "cheap"] # primary is base for loop, cheap for explore
        }
    }), encoding="utf-8")

    # For loop: primary is base, fallback is cheap (base is filtered out since it equals primary)
    client_loop = build_routed_client(tmp_path, role="loop", base_profile_name="base")
    assert isinstance(client_loop, FallbackChatClient)
    assert len(client_loop.clients) == 2

    # For explore: primary is cheap, fallback is base
    client_explore = build_routed_client(tmp_path, role="explore", base_profile_name="base")
    assert isinstance(client_explore, FallbackChatClient)
    assert len(client_explore.clients) == 2


def test_checkpoint_6_role_wiring_takes_effect(tmp_path, monkeypatch):
    import xhx_agent.models
    import xhx_agent.orchestrators.loop as loopmod
    import xhx_agent.orchestrators.subagent as submod

    # Setup dummy project files
    (tmp_path / "README.md").write_text("Hello World", encoding="utf-8")
    RuntimeApp(tmp_path).init_project()

    # Write profiles
    profiles_file = tmp_path / ".xhx" / "profiles.json"
    profiles_file.write_text(json.dumps({
        "profiles": [
            {
                "name": "base",
                "provider": "mock",
                "model": "base-mock"
            },
            {
                "name": "cheap",
                "provider": "mock",
                "model": "cheap-mock"
            }
        ]
    }), encoding="utf-8")

    # Write config.json mapping explore to cheap
    config_file = tmp_path / ".xhx" / "config.json"
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "base",
        "routing": {
            "roles": {"explore": "cheap"}
        }
    }), encoding="utf-8")

    captured_profiles = []
    original_build = xhx_agent.models.build_chat_client

    class FakeChildClient:
        def chat(self, messages, tools):
            return ChatResult(content="Child conclusion", tool_calls=[])

    parent_results = [
        ChatResult(
            content=None,
            tool_calls=[ToolCall(id="p1", name="dispatch", arguments={
                "description": "explore readme content",
                "prompt": "Read the readme file and tell me what is in it",
                "agent_type": "explore"
            })]
        ),
        ChatResult(content="Parent done", tool_calls=[])
    ]

    class StatefulParentClient:
        def __init__(self):
            self.i = 0
        def chat(self, messages, tools):
            r = parent_results[self.i]
            self.i += 1
            return r

    def mock_build_stateful(profile):
        captured_profiles.append(profile.name)
        if profile.name == "base":
            return StatefulParentClient()
        elif profile.name == "cheap":
            return FakeChildClient()
        return original_build(profile)

    monkeypatch.setattr(loopmod, "build_chat_client", mock_build_stateful)
    monkeypatch.setattr(submod, "build_chat_client", mock_build_stateful)

    res = RuntimeApp(tmp_path).run_task("do task", profile_name="base", mode="loop")
    assert res.status == "success"
    assert "base" in captured_profiles
    assert "cheap" in captured_profiles


def test_checkpoint_7_fallback_end_to_end(tmp_path, monkeypatch):
    RuntimeApp(tmp_path).init_project()
    # Write profiles specifying base-url and unset api_key_env
    profiles_file = tmp_path / ".xhx" / "profiles.json"
    profiles_file.write_text(json.dumps({
        "profiles": [
            {
                "name": "broken-openai",
                "provider": "openai-compatible",
                "base_url": "https://api.example.com/v1",
                "api_key_env": "UNSET_KEY_FOR_ROUTING_TEST",
                "model": "gpt-4"
            },
            {
                "name": "mock",
                "provider": "mock",
                "model": "mock"
            }
        ]
    }), encoding="utf-8")

    # Write config.json specifying fallback
    config_file = tmp_path / ".xhx" / "config.json"
    config_file.write_text(json.dumps({
        "version": 1,
        "default_profile": "broken-openai",
        "routing": {
            "fallback": ["mock"]
        }
    }), encoding="utf-8")

    # Build routed client
    client = build_routed_client(tmp_path, role="loop", base_profile_name="broken-openai")
    assert isinstance(client, FallbackChatClient)

    # Run chat; primary should raise ModelClientError because environment variable is unset,
    # then it falls back to mock which succeeds and returns MockResult
    res = client.chat([{"role": "user", "content": "hello"}], [])
    assert res.content is not None
    assert "Mock loop reply" in res.content
