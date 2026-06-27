"""models/routing.py 与 tools/read_file.py 单测。"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from xhx_agent.models.routing import FallbackChatClient, build_routed_client, resolve_profile_for_role
from xhx_agent.models.types import ModelClientError
from xhx_agent.tools.read_file import Params, ReadFile, _resolve_inside, read_file

# --- FallbackChatClient ---


class _OkClient:
    def __init__(self, tag: str) -> None:
        self.tag = tag

    def chat(self, messages, tools=None):
        return {"who": self.tag}


class _FailClient:
    def chat(self, messages, tools=None):
        raise ModelClientError(code="boom", message="fail")


def test_fallback_first_ok() -> None:
    fb = FallbackChatClient([_OkClient("a"), _OkClient("b")])
    assert fb.chat([])["who"] == "a"


def test_fallback_skips_failing() -> None:
    calls = []
    fb = FallbackChatClient([_FailClient(), _OkClient("b")], on_fallback=lambda i, e: calls.append(i))
    assert fb.chat([])["who"] == "b"
    assert calls == [0]


def test_fallback_all_fail_raises() -> None:
    fb = FallbackChatClient([_FailClient(), _FailClient()])
    with pytest.raises(ModelClientError):
        fb.chat([])


def test_fallback_no_clients() -> None:
    with pytest.raises(ModelClientError):
        FallbackChatClient([]).chat([])


# --- FallbackLLMClient（streaming 层，agent 主循环真正接入的那条） ---

from xhx_agent.client import FallbackLLMClient, LLMClient, NetworkError  # noqa: E402
from xhx_agent.conversation import ConversationManager  # noqa: E402
from xhx_agent.models.routing import build_agent_client  # noqa: E402
from xhx_agent.tools.base import StreamEnd, TextDelta  # noqa: E402


class _OkStream(LLMClient):
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.max_tokens: int | None = None

    def set_max_output_tokens(self, tokens: int) -> None:
        self.max_tokens = tokens

    async def stream(self, conversation, system="", tools=None):  # type: ignore[override]
        yield TextDelta(text=self.marker)
        yield StreamEnd(stop_reason="end_turn", input_tokens=1, output_tokens=1)


class _FailBeforeFirst(LLMClient):
    async def stream(self, conversation, system="", tools=None):  # type: ignore[override]
        raise NetworkError("connect failed")
        yield TextDelta(text="")  # pragma: no cover  仅为使其成为 async generator


class _FailMidStream(LLMClient):
    async def stream(self, conversation, system="", tools=None):  # type: ignore[override]
        yield TextDelta(text="partial")
        raise NetworkError("mid-stream drop")


def _collect_stream(client) -> list:
    async def _run() -> list:
        return [ev async for ev in client.stream(ConversationManager())]

    return asyncio.run(_run())


def test_fallback_llm_falls_back_before_first_event() -> None:
    calls: list[int] = []
    fb = FallbackLLMClient([_FailBeforeFirst(), _OkStream("B")], on_fallback=lambda i, e: calls.append(i))
    events = _collect_stream(fb)
    assert any(isinstance(e, TextDelta) and e.text == "B" for e in events)
    assert calls == [0]


def test_fallback_llm_no_recovery_after_stream_started() -> None:
    # 首个事件已产出（已开始流式），中途断流不再回退、原样抛出。
    fb = FallbackLLMClient([_FailMidStream(), _OkStream("B")])
    with pytest.raises(NetworkError):
        _collect_stream(fb)


def test_fallback_llm_forwards_max_tokens() -> None:
    a, b = _OkStream("A"), _OkStream("B")
    FallbackLLMClient([a, b]).set_max_output_tokens(4096)
    assert a.max_tokens == 4096 and b.max_tokens == 4096


def test_fallback_llm_empty_raises() -> None:
    with pytest.raises(ValueError):
        FallbackLLMClient([])


def test_build_agent_client_no_fallback_returns_single(monkeypatch, tmp_path: Path) -> None:
    """未配置 routing.fallback 时直接返回主 client、不包 wrapper（零行为变化保证）。"""
    from xhx_agent.models import routing

    sentinel = _OkStream("primary")
    monkeypatch.setattr("xhx_agent.client.create_client", lambda cfg: sentinel)
    fake_cfg = type("C", (), {"routing": type("R", (), {"fallback": []})()})()
    monkeypatch.setattr(routing, "load_config", lambda ws: fake_cfg)

    provider = type("P", (), {"name": "default"})()
    assert build_agent_client(tmp_path, provider) is sentinel


def test_build_agent_client_with_fallback_wraps(monkeypatch, tmp_path: Path) -> None:
    from xhx_agent.models import routing

    monkeypatch.setattr("xhx_agent.client.create_client", lambda cfg: _OkStream("c"))
    fake_cfg = type("C", (), {"routing": type("R", (), {"fallback": ["alt"]})()})()
    monkeypatch.setattr(routing, "load_config", lambda ws: fake_cfg)
    monkeypatch.setattr(routing, "get_profile", lambda ws, name: object())
    monkeypatch.setattr("xhx_agent.config.ProviderConfig.from_xhx_profile", classmethod(lambda cls, p: object()))

    provider = type("P", (), {"name": "default"})()
    result = build_agent_client(tmp_path, provider)
    assert isinstance(result, FallbackLLMClient)
    assert len(result.clients) == 2


def test_resolve_profile_for_role(tmp_path: Path) -> None:
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)
    prof = resolve_profile_for_role(tmp_path, "summarize", "default")
    assert prof is not None and prof.name


def test_build_routed_client_single(tmp_path: Path) -> None:
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)
    built = build_routed_client(
        tmp_path,
        role="chat",
        base_profile_name="default",
        build_client_func=lambda p: _OkClient(p.name),
    )
    # 无 fallback 配置 → 返回单个 client（非 wrapper）
    assert isinstance(built, _OkClient)


# --- ReadFile 工具 ---


def _run(tool: ReadFile, **kw):
    return asyncio.run(tool.execute(Params(**kw)))


def test_read_file_not_found(tmp_path: Path) -> None:
    res = _run(ReadFile(), file_path=str(tmp_path / "ghost.py"))
    assert res.is_error and "not found" in res.output


def test_read_file_success_with_line_numbers(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("l1\nl2\nl3\n", encoding="utf-8")
    res = _run(ReadFile(), file_path=str(f))
    assert res.is_error is False
    assert "1\tl1" in res.output and "3\tl3" in res.output


def test_read_file_offset_limit(tmp_path: Path) -> None:
    f = tmp_path / "b.py"
    f.write_text("\n".join(f"line{i}" for i in range(10)), encoding="utf-8")
    res = _run(ReadFile(), file_path=str(f), offset=2, limit=2)
    assert "3\tline2" in res.output and "4\tline3" in res.output
    assert "line0" not in res.output and "line5" not in res.output


def test_read_file_directory_is_error(tmp_path: Path) -> None:
    res = _run(ReadFile(), file_path=str(tmp_path))
    assert res.is_error and "not a file" in res.output


def test_legacy_read_file_func(tmp_path: Path) -> None:
    (tmp_path / "c.txt").write_text("x\ny\nz\n", encoding="utf-8")
    out = read_file(tmp_path, "c.txt", start_line=2, max_lines=1)
    assert out == "y"
    with pytest.raises(FileNotFoundError):
        _resolve_inside(tmp_path, "missing.txt")
