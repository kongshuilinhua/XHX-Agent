from __future__ import annotations

import json
from typing import Any

from xhx_agent.models.types import ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.runtime.events import emit_event

_MAX_TOOL_RESULT_CHARS = 8000


def _estimate_message_tokens(messages: list[dict]) -> int:
    """估算一组消息的 token 数（tiktoken，失败回退字符级；复用 context.compiler）。"""
    from xhx_agent.context.compiler import _estimate_tokens

    total = 0
    for m in messages:
        total += _estimate_tokens(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            total += _estimate_tokens(str(tc.get("function", {}).get("arguments", "")))
    return total


def chat_and_count(ctx: OrchestratorContext, client: Any, messages: list[dict], schemas: list[dict]) -> Any:
    """调 client.chat，累加 token 指标并在拿到 provider usage 时 emit token_usage 事件。

    - 估算路径（_estimate_message_tokens）保持不变：run_end 的 tokens_estimate 与回退仍可用。
    - 若 ChatResult 带 provider usage，则把真实 total 累加进 metrics_tracker['tokens_real']，
      并 emit 'token_usage'（cumulative_total 供状态条实时显示）。
    """
    import time

    ctx.metrics_tracker["tokens"] = ctx.metrics_tracker.get("tokens", 0) + _estimate_message_tokens(messages)
    t0 = time.perf_counter()
    result = client.chat(messages, schemas)
    duration_ms = int((time.perf_counter() - t0) * 1000)
    usage = getattr(result, "usage", None)
    if usage is not None:
        cumulative = ctx.metrics_tracker.get("tokens_real", 0) + int(usage.total or 0)
        ctx.metrics_tracker["tokens_real"] = cumulative
        model = getattr(client, "model", "")
        emit_event(
            ctx.event_callback,
            "token_usage",
            "Token usage updated.",
            prompt=int(usage.prompt or 0),
            completion=int(usage.completion or 0),
            total=int(usage.total or 0),
            cumulative_total=cumulative,
            model=model,
            duration_ms=duration_ms,
        )
    return result


def _execute_tool_call_rich(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str], dict | None]:
    """同 execute_tool_call，但额外带回 meta（结构化工具成功时含 evidence_kind/source/summary/trace_id；否则 None）。"""
    emit_event(ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}", turn=turn, tool=tc.name, arguments=tc.arguments)
    status = "success"
    summary = ""
    try:
        if tc.name == "dispatch":
            from xhx_agent.orchestrators.subagent import WRITE_AGENT_TYPES, run_subagent, run_write_subagent

            agent_type = str(tc.arguments.get("agent_type") or "explore")
            description = str(tc.arguments.get("description", ""))
            prompt = str(tc.arguments.get("prompt", ""))
            try:
                if agent_type in WRITE_AGENT_TYPES:
                    content, changed = run_write_subagent(ctx, description=description, prompt=prompt, turn=turn)
                else:
                    content = run_subagent(ctx, description=description, prompt=prompt, agent_type=agent_type, turn=turn)
                    changed = []
                status = "success"
                lines = (content or "").splitlines()
                summary = lines[0] if lines else ""
                return tc, content, changed, None
            except Exception as exc:  # noqa: BLE001
                ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": "dispatch", "error": str(exc)})
                status = "error"
                summary = str(exc)
                return tc, f"[dispatch error] {exc}", [], None
        d = ctx.kernel.tool_registry.definition(tc.name)
        if d is not None and d.is_command:
            command = str(tc.arguments.get("command") or _default_verify_command(ctx.scan))
            try:
                exec_result = ctx.kernel.run_command_tool(
                    command,
                    evidence_kind="test" if tc.name == "verify" else "command",
                    assume_yes=ctx.assume_yes,
                    confirm_callback=ctx.confirm_callback,
                    event_callback=ctx.event_callback,
                    turn=turn,
                )
                status = exec_result.status
                summary = exec_result.summary
                return tc, _render_tool_content(exec_result), list(exec_result.changed_files), None
            except Exception as exc:  # noqa: BLE001
                ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
                status = "error"
                summary = str(exc)
                return tc, f"[{tc.name} error] {exc}", [], None
        step = ToolStep(tool=tc.name, arguments=tc.arguments)
        try:
            exec_result, trace, policy = ctx.kernel.execute_tool(ctx.tool_context, step, turn, ctx.event_callback)
            if exec_result is None:
                status = "denied"
                summary = policy.reason
                return tc, f"Tool denied/blocked: {policy.reason}", [], None
            meta = None
            if trace is not None and exec_result.evidence_kind and exec_result.evidence_source and exec_result.evidence_summary:
                meta = {
                    "evidence_kind": exec_result.evidence_kind,
                    "evidence_source": exec_result.evidence_source,
                    "evidence_summary": exec_result.evidence_summary,
                    "trace_id": trace.id,
                }
            status = exec_result.status
            summary = exec_result.summary
            return tc, _render_tool_content(exec_result), list(exec_result.changed_files), meta
        except Exception as exc:  # noqa: BLE001
            ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
            status = "error"
            summary = str(exc)
            return tc, f"[{tc.name} error] {exc}", [], None
    finally:
        emit_event(
            ctx.event_callback,
            "tool_result",
            "Tool finished.",
            turn=turn,
            tool=tc.name,
            arguments=tc.arguments,
            status=status,
            summary=summary,
        )


def execute_tool_call(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str]]:
    """对外契约不变（loop 用）：丢弃 meta，返回 3 元组。"""
    tc_, content, changed, _meta = _execute_tool_call_rich(ctx, tc, turn)
    return tc_, content, changed


def _default_verify_command(scan: Any) -> str:
    langs = getattr(scan, "detected_languages", []) or []
    if "python" in langs:
        return "python -m pytest"
    if "javascript" in langs or "typescript" in langs:
        return "npm test"
    return "python -m pytest"


def _render_tool_content(result: Any) -> str:
    if result.status != "success":
        return f"[{result.tool} failed] {result.error or result.summary}"
    payload = result.trace_payload or {}
    for key in ("content", "results"):
        if key in payload:
            return f"{result.summary}\n{json.dumps(payload[key], ensure_ascii=False)[:_MAX_TOOL_RESULT_CHARS]}"
    return result.summary
