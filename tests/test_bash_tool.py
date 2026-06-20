"""tools/bash.py 单测：前台执行、错误码、server 探测。"""

from __future__ import annotations

import asyncio

from xhx_agent.tools.bash import Bash, Params, _looks_like_server


def _run(cmd: str, **kw) -> object:
    tool = Bash()
    return asyncio.run(tool.execute(Params(command=cmd, **kw)))


def test_echo_success() -> None:
    res = _run("echo hello123")
    assert res.is_error is False
    assert "hello123" in res.output


def test_nonzero_exit_is_error() -> None:
    # 跨平台：python 退出码 7
    res = _run('python -c "import sys; sys.exit(7)"')
    assert res.is_error is True


def test_looks_like_server() -> None:
    assert _looks_like_server("python -m http.server 8000") is True
    assert _looks_like_server("npm run dev") is True
    assert _looks_like_server("uvicorn app:app") is True
    assert _looks_like_server("echo hello") is False
    assert _looks_like_server("ls -la") is False


def test_category_is_command() -> None:
    assert Bash().category == "command"
    assert Bash().is_read_only is False
