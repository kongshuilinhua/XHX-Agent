"""OpenAI 兼容的对话补全客户端（规划层用）：把任务 + 上下文包发给 LLM，解析回结构化 ModelPlan。

只依赖 /chat/completions，支持流式与非流式。容错是重点：HTTP 错误、非 JSON 响应、多模态 content、
markdown 代码围栏、混杂散文里的 JSON 对象，都被规整成结构化的 ModelClientError 或干净的 plan。
API key 只从环境变量名（api_key_env 指向的变量）读取，从不硬编码。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from xhx_agent.context.pack import ContextPack
from xhx_agent.models.types import ChatResult, ModelClientError, ModelPlan, ToolCall

ModelDeltaCallback = Callable[[str], None]


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
    """精简的 OpenAI 兼容对话补全客户端，供规划层调用。"""

    def __init__(
        self,
        *,
        base_url: str,
        api_key_env: str,
        model: str,
        temperature: float = 0.2,
        stream: bool = False,
        timeout_seconds: float = 60,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env
        self.model = model
        self.temperature = temperature
        self.stream = stream
        self.http_client = http_client or httpx.Client(timeout=timeout_seconds)
        # 外部（orchestrator）可挂一个回调来接 tool-calling chat() 的流式 content 增量；
        # 只有同时 stream=True 且挂了回调时 chat() 才走流式路径——否则与非流式结果完全一致（零行为变更）。
        self.delta_callback: ModelDeltaCallback | None = None

    def set_delta_callback(self, callback: ModelDeltaCallback | None) -> None:
        """挂载流式 content 增量回调（loop 用来把 token 实时喂给 Live 状态行）。"""
        self.delta_callback = callback

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ChatResult:
        api_key = os.getenv(self.api_key_env)
        if not api_key:
            raise ModelClientError(
                code="missing_api_key",
                message=f"Missing API key environment variable: {self.api_key_env}",
                details={"api_key_env": self.api_key_env},
            )
        payload: dict[str, Any] = {"model": self.model, "temperature": self.temperature, "messages": messages}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        # 仅当 profile stream=True 且挂了 delta 回调时才流式，否则与非流式结果完全一致。
        if self.stream and self.delta_callback is not None:
            return self._chat_stream(payload, api_key)
        return self._chat_nonstream(payload, api_key)

    def _chat_nonstream(self, payload: dict[str, Any], api_key: str) -> ChatResult:
        try:
            response = self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ModelClientError(
                code="network_error", message=f"Chat request failed: {exc}", details={"error": str(exc)}
            ) from exc
        if response.status_code >= 400:
            raise ModelClientError(
                code="http_error",
                message=f"Chat request returned HTTP {response.status_code}.",
                details={"status_code": response.status_code, "body": response.text[:1000]},
            )
        try:
            message = response.json()["choices"][0]["message"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelClientError(
                code="invalid_response",
                message="Chat response missing choices[0].message.",
                details={"body": response.text[:1000]},
            ) from exc
        return _message_to_chat_result(message)

    def _chat_stream(self, payload: dict[str, Any], api_key: str) -> ChatResult:
        """流式 tool-calling chat：实时把 content 增量喂给 delta_callback，并按 index 拼装分片的 tool_calls。"""
        stream_payload = {**payload, "stream": True}
        content_parts: list[str] = []
        tool_frags: dict[int, dict[str, str]] = {}
        try:
            with self.http_client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=stream_payload,
            ) as response:
                if response.status_code >= 400:
                    raise ModelClientError(
                        code="http_error",
                        message=f"Chat request returned HTTP {response.status_code}.",
                        details={
                            "status_code": response.status_code,
                            "body": response.read().decode("utf-8", errors="replace")[:1000],
                        },
                    )
                for raw_line in response.iter_lines():
                    if not raw_line or not raw_line.startswith("data:"):
                        continue
                    line = raw_line.removeprefix("data:").strip()
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # 容忍 keep-alive / 注释 / 半行
                    self._consume_stream_delta(data, content_parts, tool_frags)
        except ModelClientError:
            raise
        except httpx.HTTPError as exc:
            raise ModelClientError(
                code="network_error", message=f"Chat request failed: {exc}", details={"error": str(exc)}
            ) from exc
        return _assemble_stream_chat(content_parts, tool_frags)

    def _consume_stream_delta(
        self, data: dict[str, Any], content_parts: list[str], tool_frags: dict[int, dict[str, str]]
    ) -> None:
        try:
            delta = data["choices"][0].get("delta", {})
        except (KeyError, IndexError, TypeError):
            return
        if not isinstance(delta, dict):
            return
        text = _normalize_chat_content(delta.get("content"))
        if text:
            content_parts.append(text)
            if self.delta_callback is not None:
                self.delta_callback(text)
        for frag in delta.get("tool_calls") or []:
            if not isinstance(frag, dict):
                continue
            slot = tool_frags.setdefault(int(frag.get("index", 0) or 0), {"id": "", "name": "", "args": ""})
            if frag.get("id"):
                slot["id"] = frag["id"]
            fn = frag.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"] += fn["arguments"]

    def plan(
        self,
        task: str,
        context_pack: ContextPack | dict[str, Any],
        delta_callback: ModelDeltaCallback | None = None,
    ) -> ModelPlan:
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
            "stream": self.stream,
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
        if self.stream:
            return self._stream_plan(payload, api_key, delta_callback)
        return self._non_stream_plan(payload, api_key)

    def summarize(self, text: str) -> str:
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
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Summarize the tool-call history below in 1-2 concise sentences for an "
                        "engineering assistant. State what was done and note any failures."
                    ),
                },
                {"role": "user", "content": text},
            ],
        }
        response = self.http_client.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        if response.status_code >= 400:
            raise ModelClientError(
                code="http_error",
                message=f"Summarize request returned HTTP {response.status_code}.",
                details={"status_code": response.status_code},
            )
        return _extract_chat_content(response.json()).strip()

    def _non_stream_plan(self, payload: dict[str, Any], api_key: str) -> ModelPlan:
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

    def _stream_plan(
        self,
        payload: dict[str, Any],
        api_key: str,
        delta_callback: ModelDeltaCallback | None = None,
    ) -> ModelPlan:
        try:
            with self.http_client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    raise ModelClientError(
                        code="http_error",
                        message=f"Model request returned HTTP {response.status_code}.",
                        details={
                            "status_code": response.status_code,
                            "body": response.read().decode("utf-8", errors="replace")[:1000],
                        },
                    )
                content = _collect_stream_content(response, delta_callback)
        except ModelClientError:
            raise
        except httpx.HTTPError as exc:
            raise ModelClientError(
                code="network_error",
                message=f"Model request failed: {exc}",
                details={"error": str(exc)},
            ) from exc

        if not content.strip():
            raise ModelClientError(
                code="invalid_response",
                message="Model streaming response content was empty.",
                details={},
            )
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


def _collect_stream_content(response: httpx.Response, delta_callback: ModelDeltaCallback | None = None) -> str:
    chunks: list[str] = []
    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("data:"):
            line = line.removeprefix("data:").strip()
        if line == "[DONE]":
            break
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                code="invalid_response",
                message="Model streaming response included invalid JSON.",
                details={"line": line[:1000]},
            ) from exc
        delta = _extract_stream_delta(data)
        if not delta:
            continue
        chunks.append(delta)
        if delta_callback is not None:
            delta_callback(delta)
    return "".join(chunks)


def _extract_stream_delta(data: dict[str, Any]) -> str:
    try:
        delta = data["choices"][0].get("delta", {})
    except (KeyError, IndexError, TypeError):
        return ""
    if not isinstance(delta, dict):
        return ""
    content = delta.get("content")
    return _normalize_chat_content(content)


def _message_to_chat_result(message: dict[str, Any]) -> ChatResult:
    """把非流式 choices[0].message 转成 ChatResult（content + 解析后的 tool_calls）。"""
    tool_calls: list[ToolCall] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", {})
        args = raw_args
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError as exc:
                raise ModelClientError(
                    code="invalid_tool_arguments",
                    message=f"tool_call arguments not valid JSON: {raw_args[:200]}",
                    details={"arguments": raw_args[:1000]},
                ) from exc
        tool_calls.append(ToolCall(id=tc.get("id", ""), name=fn.get("name", ""), arguments=args or {}))
    content = message.get("content")
    return ChatResult(content=content if isinstance(content, str) else None, tool_calls=tool_calls)


def _assemble_stream_chat(content_parts: list[str], tool_frags: dict[int, dict[str, str]]) -> ChatResult:
    """把流式累积的 content 片段与按 index 拼接的 tool_call 片段组装成最终 ChatResult。"""
    tool_calls: list[ToolCall] = []
    for index in sorted(tool_frags):
        slot = tool_frags[index]
        if not slot["name"]:
            continue
        raw = slot["args"]
        try:
            args = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError as exc:
            raise ModelClientError(
                code="invalid_tool_arguments",
                message=f"streamed tool_call arguments not valid JSON: {raw[:200]}",
                details={"arguments": raw[:1000]},
            ) from exc
        tool_calls.append(ToolCall(id=slot["id"], name=slot["name"], arguments=args))
    content = "".join(content_parts)
    return ChatResult(content=content or None, tool_calls=tool_calls)


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
    """从可能夹带散文 / markdown 围栏的模型输出里，抠出第一个完整的 JSON 对象。

    用括号深度匹配（且正确跳过字符串内的转义引号），比正则更稳；找不到完整对象则抛 invalid_plan_json。
    """
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
