from __future__ import annotations

import os

import httpx
import pytest

from xhx_agent.context.pack import ContextPack
from xhx_agent.models.openai_compatible import (
    OpenAICompatibleClient,
    _context_payload,
    _excerpt_around,
    _extract_stream_delta,
    _normalize_chat_content,
    _parse_plan_content,
)
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
        assert request.headers["Authorization"] == "Bearer test-key"
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
                                {
                                    "type": "text",
                                    "text": '"steps":[{"tool":"read_file","arguments":{"path":"README.md"}}]}',
                                },
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


def _client(handler, *, stream: bool = False) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        stream=stream,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


# --- summarize() -----------------------------------------------------------------------------


def test_summarize_returns_stripped_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(
        lambda _r: httpx.Response(200, json={"choices": [{"message": {"content": "  did X; 1 test failed.  "}}]})
    )

    assert client.summarize("tool history") == "did X; 1 test failed."


def test_summarize_missing_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XHX_TEST_API_KEY", raising=False)
    client = _client(lambda _r: httpx.Response(200, json={}))

    with pytest.raises(ModelClientError) as exc:
        client.summarize("tool history")

    assert exc.value.code == "missing_api_key"


def test_summarize_http_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(500, text="boom"))

    with pytest.raises(ModelClientError) as exc:
        client.summarize("tool history")

    assert exc.value.code == "http_error"
    assert exc.value.details["status_code"] == 500


# --- plan() non-stream error paths -----------------------------------------------------------


def test_plan_network_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ModelClientError) as exc:
        _client(handler).plan("read", {"detected_languages": []})

    assert exc.value.code == "network_error"


def test_plan_non_json_response_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(200, content=b"<html>not json</html>"))

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_response"


def test_plan_missing_choices_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(200, json={"object": "chat.completion"}))

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_response"


def test_plan_empty_content_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(200, json={"choices": [{"message": {"content": "   "}}]}))

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_response"


def test_plan_schema_invalid_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    # Valid JSON, but a "continue" plan with no steps violates the ModelPlan schema.
    client = _client(
        lambda _r: httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"summary":"x","status":"continue","steps":[]}'}}]},
        )
    )

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_plan_schema"


# --- plan() streaming error paths ------------------------------------------------------------


def test_stream_http_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(429, text="rate limited"), stream=True)

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "http_error"
    assert exc.value.details["status_code"] == 429


def test_stream_empty_content_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(200, content="data: [DONE]\n\n"), stream=True)

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_response"


def test_stream_invalid_json_line_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    client = _client(lambda _r: httpx.Response(200, content="data: {not valid json\n\n"), stream=True)

    with pytest.raises(ModelClientError) as exc:
        client.plan("read", {"detected_languages": []})

    assert exc.value.code == "invalid_response"


# --- pure parsing helpers --------------------------------------------------------------------


def test_normalize_chat_content_handles_mixed_parts() -> None:
    assert _normalize_chat_content("plain string") == "plain string"
    # A multimodal content array mixes raw strings, {"text":...}, {"content":...}, and ignorables.
    mixed = _normalize_chat_content(
        [
            {"text": "alpha"},
            "beta",
            12345,  # non-str, non-dict -> ignored
            {"content": "gamma"},  # nested content string
            {"image_url": "http://x"},  # dict without text/content -> ignored
        ]
    )
    assert mixed == "alpha\nbeta\ngamma"
    # Unsupported top-level types normalize to an empty string.
    assert _normalize_chat_content(42) == ""


def test_extract_stream_delta_handles_malformed_chunks() -> None:
    assert _extract_stream_delta({"choices": [{"delta": {"content": "hi"}}]}) == "hi"
    assert _extract_stream_delta({}) == ""  # missing choices
    assert _extract_stream_delta({"choices": [{"delta": "oops"}]}) == ""  # delta is not a dict


def test_excerpt_around_adds_ellipsis_in_the_middle() -> None:
    text = "x" * 500
    middle = _excerpt_around(text, 250, radius=10)
    assert middle.startswith("...")
    assert middle.endswith("...")
    # At the very start there is no leading ellipsis.
    assert not _excerpt_around(text, 0, radius=10).startswith("...")


def test_parse_plan_content_respects_escaped_quotes_in_strings() -> None:
    # The brace-matcher must not treat an escaped quote inside a JSON string as a string boundary.
    plan = _parse_plan_content('{"summary":"say \\"hi\\" now","status":"done","steps":[]}')
    assert plan.summary == 'say "hi" now'


def test_context_payload_accepts_context_pack_object() -> None:
    pack = ContextPack(task="demo", budget_tokens=100, used_tokens_estimate=0)
    payload = _context_payload(pack)
    assert isinstance(payload, dict)
    assert payload == pack.to_model_payload()
    # A plain dict passes through unchanged.
    assert _context_payload({"k": "v"}) == {"k": "v"}


def test_stream_network_error_is_structured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ModelClientError) as exc:
        _client(handler, stream=True).plan("read", {"detected_languages": []})

    assert exc.value.code == "network_error"


def test_stream_skips_empty_deltas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    chunks = [
        'data: {"choices":[{"delta":{}}]}\n\n',  # empty delta -> skipped, not appended
        'data: {"choices":[{"delta":{"content":"{\\"summary\\":\\"ok\\",\\"status\\":\\"done\\",\\"steps\\":[]}"}}]}\n\n',
        "data: [DONE]\n\n",
    ]
    client = _client(lambda _r: httpx.Response(200, content="".join(chunks)), stream=True)

    plan = client.plan("read", {"detected_languages": []})

    assert plan.status == "done"


def test_parse_plan_content_balanced_but_invalid_json() -> None:
    # Braces balance, so the object extractor returns a candidate, but it is not valid JSON.
    with pytest.raises(ModelClientError) as exc:
        _parse_plan_content("{not: valid, json}")

    assert exc.value.code == "invalid_plan_json"


def test_extract_json_object_strips_double_markdown_fence() -> None:
    plan = _parse_plan_content('```\n```json\n{"summary":"ok","status":"done","steps":[]}\n```\n```')

    assert plan.status == "done"


# --- opt-in live integration -----------------------------------------------------------------


@pytest.mark.live
def test_live_openai_plan_smoke() -> None:
    """End-to-end smoke test against a real OpenAI-compatible endpoint.

    Skipped by default. To run it::

        XHX_LIVE_BASE_URL=https://api.openai.com/v1 \\
        XHX_LIVE_API_KEY_ENV=OPENAI_API_KEY \\
        XHX_LIVE_MODEL=gpt-4o-mini \\
        uv run pytest -m live

    The actual API key lives in the env var named by XHX_LIVE_API_KEY_ENV, so the secret is
    never hard-coded. The test no-ops (skips) whenever the live config is absent, which keeps
    CI deterministic and free of network/API dependencies.
    """
    base_url = os.getenv("XHX_LIVE_BASE_URL")
    api_key_env = os.getenv("XHX_LIVE_API_KEY_ENV")
    model = os.getenv("XHX_LIVE_MODEL")
    if not (base_url and api_key_env and model and os.getenv(api_key_env)):
        pytest.skip("Live LLM config not set (XHX_LIVE_BASE_URL / XHX_LIVE_API_KEY_ENV / XHX_LIVE_MODEL).")

    client = OpenAICompatibleClient(base_url=base_url, api_key_env=api_key_env, model=model)
    plan = client.plan("List the Python files in this repository.", {"detected_languages": ["python"]})

    assert plan.summary
    assert plan.status in {"continue", "done"}


def test_chat_captures_usage_nonstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok", "tool_calls": []}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16},
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert result.usage is not None
    assert result.usage.prompt == 11
    assert result.usage.completion == 5
    assert result.usage.total == 16


def test_chat_stream_captures_usage_and_requests_include_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")
    captured_body = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured_body.update(_json.loads(request.read().decode("utf-8")))
        body = (
            'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
            'data: {"choices":[],"usage":{"prompt_tokens":7,"completion_tokens":2,"total_tokens":9}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        stream=True,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.set_delta_callback(lambda _text: None)

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert captured_body.get("stream") is True
    assert captured_body.get("stream_options") == {"include_usage": True}
    assert result.content == "hello"
    assert result.usage is not None
    assert result.usage.total == 9


def test_chat_nonstream_captures_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "ok",
                            "reasoning_content": "thinking process",
                            "tool_calls": [],
                        }
                    }
                ],
            },
        )

    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])
    assert result.reasoning == "thinking process"


def test_chat_stream_captures_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XHX_TEST_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
            'data: {"choices":[{"delta":{"reasoning_content":"ing"}}]}\n\n'
            'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, text=body)

    deltas = []
    client = OpenAICompatibleClient(
        base_url="https://api.example.com/v1",
        api_key_env="XHX_TEST_API_KEY",
        model="demo-model",
        stream=True,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.set_delta_callback(deltas.append)

    result = client.chat([{"role": "user", "content": "hi"}], tools=[])

    assert result.content == "ok"
    assert result.reasoning == "thinking"
    # Content delta callback should ONLY receive "ok", not "think" or "ing"
    assert deltas == ["ok"]

