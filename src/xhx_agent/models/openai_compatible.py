from __future__ import annotations

import json
import os
from typing import Any

import httpx
from pydantic import ValidationError

from xhx_agent.context.pack import ContextPack
from xhx_agent.models.types import ModelClientError, ModelPlan


SYSTEM_PROMPT = """You are the planning layer of xhx-agent.
Return only one JSON object. Do not include prose outside JSON.

Schema:
{
  "summary": "short plan summary",
  "status": "continue",
  "steps": [
    {"tool": "search", "arguments": {"query": "text", "glob": "*.py"}},
    {"tool": "read_file", "arguments": {"path": "relative/path"}},
    {"tool": "apply_patch", "arguments": {"patch": "*** Begin Patch\\n...\\n*** End Patch\\n"}}
  ]
}

Valid status values:
- "continue": more tool work is needed; steps must contain at least one tool call.
- "done": no more tool work is useful; steps must be [].

Allowed tools are search, read_file, and apply_patch.
Use relative paths only.
Do not include terminal commands; xhx-agent routes verification after changes.
If there is not enough evidence to patch, return only search/read_file steps.
If the task is complete or no more tool work is useful, return {"summary":"...","status":"done","steps":[]}.
Use only the supplied context pack; do not assume unread files.
"""


class OpenAICompatibleClient:
    """Minimal OpenAI-compatible chat completions client for v0.1 planning."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str,
        model: str,
        temperature: float = 0.2,
        timeout_seconds: float = 60,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model
        self.temperature = temperature
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)

    def plan(self, task: str, context_pack: ContextPack | dict[str, Any]) -> ModelPlan:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelClientError(
                code="missing_api_key",
                message=f"Missing API key environment variable: {self.api_key_env}",
                details={"api_key_env": self.api_key_env},
            )

        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "stream": False,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "task": task,
                            "context_pack": _context_payload(context_pack),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
            ],
        }
        try:
            response = self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ModelClientError(
                code="network_error",
                message=f"Model request failed: {exc}",
                details={"error": str(exc)},
            ) from exc

        if response.status_code >= 400:
            raise ModelClientError(
                code="http_error",
                message=f"Model request returned HTTP {response.status_code}.",
                details={"status_code": response.status_code, "body": response.text[:1000]},
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ModelClientError(
                code="invalid_response",
                message="Model response was not valid JSON.",
                details={"body": response.text[:1000]},
            ) from exc

        content = _extract_chat_content(data)
        return _parse_plan_content(content)


def _context_payload(context_pack: ContextPack | dict[str, Any]) -> dict[str, Any]:
    if isinstance(context_pack, ContextPack):
        return context_pack.to_model_payload()
    return context_pack


def _extract_chat_content(data: dict[str, Any]) -> str:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelClientError(
            code="invalid_response",
            message="Model response did not include choices[0].message.content.",
            details={"response": data},
        ) from exc
    normalized = _normalize_chat_content(content)
    if not normalized.strip():
        raise ModelClientError(
            code="invalid_response",
            message="Model response content was empty.",
            details={"content": content},
        )
    return normalized


def _normalize_chat_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for part in content:
            if isinstance(part, str):
                chunks.append(part)
                continue
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                chunks.append(text)
                continue
            nested_content = part.get("content")
            if isinstance(nested_content, str):
                chunks.append(nested_content)
        return "\n".join(chunks)
    return ""


def _parse_plan_content(content: str) -> ModelPlan:
    json_text = _extract_json_object(content)
    try:
        raw_plan = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ModelClientError(
            code="invalid_plan_json",
            message=f"Model plan content was not valid JSON at line {exc.lineno}, column {exc.colno}.",
            details={
                "line": exc.lineno,
                "column": exc.colno,
                "position": exc.pos,
                "excerpt": _excerpt_around(json_text, exc.pos),
            },
        ) from exc
    try:
        return ModelPlan.model_validate(raw_plan)
    except ValidationError as exc:
        raise ModelClientError(
            code="invalid_plan_schema",
            message="Model plan JSON did not match the expected schema.",
            details={"errors": exc.errors(), "plan": raw_plan},
        ) from exc


def _extract_json_object(content: str) -> str:
    stripped = _strip_markdown_fence(content.strip())
    if stripped.startswith("```"):
        stripped = _strip_markdown_fence(stripped)
    start = stripped.find("{")
    if start == -1:
        raise ModelClientError(
            code="invalid_plan_json",
            message="Model plan content did not contain a JSON object.",
            details={"excerpt": stripped[:1000]},
        )
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    raise ModelClientError(
        code="invalid_plan_json",
        message="Model plan content did not contain a complete JSON object.",
        details={
            "line": stripped.count("\n", 0, len(stripped)) + 1,
            "column": len(stripped.rsplit("\n", 1)[-1]) + 1,
            "position": len(stripped),
            "excerpt": stripped[start : start + 1000],
        },
    )


def _strip_markdown_fence(content: str) -> str:
    lines = content.splitlines()
    if not lines or not lines[0].strip().startswith("```"):
        return content
    lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _excerpt_around(text: str, position: int, radius: int = 160) -> str:
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    prefix = "..." if start else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"
