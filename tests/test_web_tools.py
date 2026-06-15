from pathlib import Path

import httpx
import pytest

from xhx_agent.tools.web import _is_safe_url, web_fetch


def test_is_safe_url() -> None:
    # Safe URLs
    assert _is_safe_url("http://example.com")[0] is True
    assert _is_safe_url("https://example.com")[0] is True
    assert _is_safe_url("https://google.com/search?q=test")[0] is True

    # Bad schemes
    assert _is_safe_url("file:///etc/passwd")[0] is False
    assert _is_safe_url("ftp://1.1.1.1")[0] is False
    assert _is_safe_url("gopher://localhost")[0] is False

    # Private/Loopback IPs
    assert _is_safe_url("http://127.0.0.1")[0] is False
    assert _is_safe_url("http://localhost")[0] is False
    assert _is_safe_url("http://192.168.1.1")[0] is False
    assert _is_safe_url("http://10.0.0.1")[0] is False
    assert _is_safe_url("http://172.16.0.1")[0] is False
    assert _is_safe_url("http://169.254.169.254")[0] is False
    assert _is_safe_url("http://[::1]")[0] is False


def test_web_fetch_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_send(self_client, request, **kwargs):
        return httpx.Response(
            status_code=200,
            content=b"<html><body><h1>Hello World</h1><script>alert(1)</script></body></html>",
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", mock_send)

    res = web_fetch("https://example.com")
    assert "Hello World" in res
    assert "alert" not in res  # script stripped


def test_web_fetch_max_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    def mock_send(self_client, request, **kwargs):
        return httpx.Response(status_code=200, content=b"A" * 1000, request=request)

    monkeypatch.setattr(httpx.Client, "send", mock_send)

    res = web_fetch("https://example.com", max_bytes=10)
    assert len(res) <= 15  # should be truncated near 10 bytes


def test_web_fetch_redirect_loop_and_ssrf(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect_count = 0

    def mock_send(self_client, request, **kwargs):
        nonlocal redirect_count
        redirect_count += 1
        if redirect_count == 1:
            return httpx.Response(status_code=302, headers={"Location": "http://127.0.0.1"}, request=request)
        return httpx.Response(status_code=200, content=b"should not reach", request=request)

    monkeypatch.setattr(httpx.Client, "send", mock_send)

    with pytest.raises(Exception) as exc:
        web_fetch("https://example.com")
    assert any(word in str(exc.value).lower() for word in ("disallowed", "loopback", "safe", "private"))


def test_web_search_basic(monkeypatch: pytest.MonkeyPatch) -> None:
    from xhx_agent.tools.web import web_search

    called = False

    def mock_send(self_client, request, **kwargs):
        nonlocal called
        called = True
        assert str(request.url) == "https://api.tavily.com/search"
        import json

        body = json.loads(request.read().decode("utf-8"))
        assert body["api_key"] == "test-key"
        assert body["query"] == "weather in shanghai"

        response_data = {
            "results": [
                {
                    "title": "Shanghai Weather Forecast",
                    "url": "https://weather.com/shanghai",
                    "content": "Sunny and warm",
                }
            ]
        }
        return httpx.Response(status_code=200, json=response_data, request=request)

    monkeypatch.setattr(httpx.Client, "send", mock_send)

    results = web_search("weather in shanghai", api_key="test-key", max_results=5)
    assert called is True
    assert len(results) == 1
    assert results[0]["title"] == "Shanghai Weather Forecast"
    assert results[0]["url"] == "https://weather.com/shanghai"
    assert results[0]["content"] == "Sunny and warm"


def test_web_search_runner_missing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from xhx_agent.models.types import ToolStep
    from xhx_agent.tools.registry import ToolContext

    (tmp_path / ".xhx").mkdir()
    from xhx_agent.runtime.config import write_default_config

    write_default_config(tmp_path)

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    called = False

    def mock_post(url, json, **kwargs):
        nonlocal called
        called = True
        return httpx.Response(status_code=200, json={"results": []})

    monkeypatch.setattr(httpx, "post", mock_post)

    from xhx_agent.tools.registry import default_tool_registry

    reg = default_tool_registry()

    context = ToolContext(workspace=tmp_path)
    step = ToolStep(tool="web_search", arguments={"query": "test query"})
    result = reg.execute(context, step)

    assert called is False
    assert result.status == "failed"
    assert "未配置" in result.summary


def test_web_search_runner_reads_key_from_original_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """run 在 worktree 里跑、worktree 无 gitignored 的 .xhx；key 在原始 workspace 的项目配置里。

    runner 必须用 original_workspace 解析 key，而不是 worktree（否则真模型联调里 web_search 拿不到 key）。
    """
    import json

    from xhx_agent.models.types import ToolStep
    from xhx_agent.tools.registry import ToolContext, default_tool_registry

    # 原始项目根：.xhx/config.json 里带 key
    orig = tmp_path / "orig"
    (orig / ".xhx").mkdir(parents=True)
    (orig / ".xhx" / "config.json").write_text(
        json.dumps(
            {"version": 1, "web_search": {"provider": "tavily", "tavily_api_key": "orig-key", "max_results": 5}}
        ),
        encoding="utf-8",
    )
    # 隔离 worktree：没有 .xhx（模拟 gitignored 被排除）
    worktree = tmp_path / "wt"
    worktree.mkdir()

    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    seen: dict[str, str] = {}

    def mock_send(self_client, request, **kwargs):
        body = json.loads(request.read().decode("utf-8"))
        seen["api_key"] = body["api_key"]
        return httpx.Response(
            status_code=200,
            json={"results": [{"title": "T", "url": "https://x", "content": "c"}]},
            request=request,
        )

    monkeypatch.setattr(httpx.Client, "send", mock_send)

    reg = default_tool_registry()
    context = ToolContext(workspace=worktree, original_workspace=orig)
    step = ToolStep(tool="web_search", arguments={"query": "q"})
    result = reg.execute(context, step)

    assert result.status == "success"
    assert seen.get("api_key") == "orig-key"  # 从 original_workspace 读到，而非 worktree
