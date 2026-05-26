from __future__ import annotations

import httpx
import pytest

from xhx_agent.models.openai_compatible import OpenAICompatibleClient
from xhx_agent.models.openai_compatible import _parse_plan_content
from xhx_agent.models.types import ModelClientError


def test_openai_compatible_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XHX_TEST_API_KEY", raising=False)
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
    )

    with pytest.raises(ModelClientError) as exc:
        client.plan("analyze repo", {"detected_languages": []})

    assert exc.value.code == "missing_api_key"
    assert "XHX_TEST_API_KEY" in exc.value.message


def test_openai_compatible_parses_model_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    captured_body = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        body = request.read().decode("utf-8")
        captured_body = body
        assert "/chat/completions" in str(request.url)
        assert "Bearer test-key" == request.headers["Authorization"]
        assert "demo-model" in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary":"Read README","steps":[{"tool":"read_file","arguments":{"path":"README.md"}}]}'
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    plan = client.plan("read readme", {"detected_languages": []})

    assert plan.summary == "Read README"
    assert plan.steps[0].tool == "read_file"
    assert plan.steps[0].arguments == {"path": "README.md"}
    assert "context_pack" in captured_body
    assert "detected_languages" in captured_body


def test_openai_compatible_parses_fenced_json_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": """```json
{"summary":"Done","status":"done","steps":[]}
```"""
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    plan = client.plan("read readme", {"detected_languages": []})

    assert plan.status == "done"
    assert plan.steps == []


def test_openai_compatible_parses_segmented_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": '{"summary":"Read README",'},
                                {"type": "text", "text": '"steps":[{"tool":"read_file","arguments":{"path":"README.md"}}]}'},
                            ]
                        }
                    }
                ]
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    plan = client.plan("read readme", {"detected_languages": []})

    assert plan.steps[0].tool == "read_file"


def test_openai_compatible_streams_model_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    chunks = [
        'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"Read"}}]}\n\n',
        'data: {"choices":[{"delta":{"content":" README\\",\\"steps\\":[{\\"tool\\":\\"read_file\\",\\"arguments\\":{\\"path\\":\\"README.md\\"}}]}"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    captured_body = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_body
        captured_body = request.read().decode("utf-8")
        return httpx.Response(200, content="".join(chunks))

    deltas: list[str] = []
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        stream=True,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    plan = client.plan("read readme", {"detected_languages": []}, delta_callback=deltas.append)

    assert plan.summary == "Read README"
    assert plan.steps[0].tool == "read_file"
    assert deltas == [
        '{"summary":"Read',
        ' README","steps":[{"tool":"read_file","arguments":{"path":"README.md"}}]}',
    ]
    assert '"stream":true' in captured_body


def test_parse_plan_content_ignores_trailing_prose_after_json() -> None:
    plan = _parse_plan_content(
        'Here is the plan:\n{"summary":"Search term","steps":[{"tool":"search","arguments":{"query":"demo {term}"}}]}\nDone.'
    )

    assert plan.summary == "Search term"
    assert plan.steps[0].tool == "search"


def test_openai_compatible_http_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(401, text="nope"))),
    )

    with pytest.raises(ModelClientError) as exc:
        client.plan("read readme", {"detected_languages": []})

    assert exc.value.code == "http_error"
    assert "HTTP 401" in exc.value.message


def test_openai_compatible_invalid_plan_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200,
                    json={"choices": [{"message": {"content": "not json"}}]},
                )
            )
        ),
    )

    with pytest.raises(ModelClientError) as exc:
        client.plan("read readme", {"detected_languages": []})

    assert exc.value.code == "invalid_plan_json"


def test_invalid_json_error_includes_location() -> None:
    with pytest.raises(ModelClientError) as exc:
        _parse_plan_content('{"summary":"bad","steps":[')

    assert exc.value.code == "invalid_plan_json"
    assert "line" in exc.value.details
    assert "column" in exc.value.details
    assert "excerpt" in exc.value.details
