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
