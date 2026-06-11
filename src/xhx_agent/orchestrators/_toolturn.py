from __future__ import annotations

import json
from typing import Any

from xhx_agent.models.types import ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.runtime.events import emit_event

_MAX_TOOL_RESULT_CHARS = 8000


def execute_tool_call(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str]]:
    """执行单个 tool_call：命令工具走 kernel.run_command_tool，结构化工具走 kernel.execute_tool；
    逐工具 try/except，错误转成可回喂模型的文本。返回 (tc, content, changed_files)。"""
    emit_event(ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}", turn=turn, tool=tc.name)
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
            return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
        except Exception as exc:  # noqa: BLE001
            ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
            return tc, f"[{tc.name} error] {exc}", []
    step = ToolStep(tool=tc.name, arguments=tc.arguments)
    try:
        exec_result, _trace, policy = ctx.kernel.execute_tool(ctx.tool_context, step, turn, ctx.event_callback)
        if exec_result is None:
            return tc, f"Tool denied/blocked: {policy.reason}", []
        return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
    except Exception as exc:  # noqa: BLE001
        ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
        return tc, f"[{tc.name} error] {exc}", []


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
