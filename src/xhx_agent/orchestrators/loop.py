from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from xhx_agent.models import build_chat_client
from xhx_agent.models.types import ModelClientError, ToolStep
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event
from xhx_agent.runtime.session import save_transcript

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

LOOP_SYSTEM_PROMPT = (
    "You are xhx-agent, a coding agent operating inside a local repository.\n"
    "Answer the user's questions directly in natural language. Only call tools when code work is needed.\n"
    "Use relative paths only. All writes go through apply_patch. If evidence is insufficient, "
    "read_file/search first before patching. Do not assume unread files."
)
_MAX_TOOL_RESULT_CHARS = 8000


class LoopOrchestrator:
    """loop 范式：ReAct tool-use 统一循环（Claude Code 式）。

    模型回纯文本=对话回答即结束；回 tool_calls=经 kernel 执行、结果作为 role:tool 消息追加、再循环。
    """

    name = "loop"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.evidence.report import write_report
        from xhx_agent.runtime.app import RunResult

        client = build_chat_client(ctx.profile)
        schemas = ctx.kernel.tool_registry.tool_schemas()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": LOOP_SYSTEM_PROMPT + "\n\n" + render_xhx_md(ctx.scan)},
        ]
        if ctx.prior_messages:
            messages.extend(m for m in ctx.prior_messages if m.get("role") != "system")
        messages.append({"role": "user", "content": ctx.task})
        changed_files: list[str] = []
        risks: list[str] = []
        max_turns = load_config(ctx.original_workspace).max_loop_turns
        answer: str | None = None
        status = "success"
        turns_used = 0

        for turn in range(1, max_turns + 1):
            turns_used = turn
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                risks.append("Run cancelled before model call.")
                break
            try:
                result = client.chat(messages, schemas)
            except ModelClientError as exc:
                ctx.evidence.write_trace("model_error", {"turn": turn, **exc.to_trace_payload()})
                emit_event(ctx.event_callback, "model_error", exc.message, turn=turn, code=exc.code)
                status = "failed"
                risks.append(exc.message)
                break

            if not result.tool_calls:
                answer = result.content or ""
                messages.append({"role": "assistant", "content": answer})
                emit_event(
                    ctx.event_callback,
                    "model_plan",
                    f"loop answer [turn {turn}]",
                    turn=turn,
                    step_count=0,
                    status="done",
                )
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": result.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                        }
                        for tc in result.tool_calls
                    ],
                }
            )

            def _exec_one(tc, turn=turn):
                emit_event(
                    ctx.event_callback, "tool_start", f"Tool execution started: {tc.name}", turn=turn, tool=tc.name
                )
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
                    exec_result, _trace, policy = ctx.kernel.execute_tool(
                        ctx.tool_context, step, turn, ctx.event_callback
                    )
                    if exec_result is None:
                        return tc, f"Tool denied/blocked: {policy.reason}", []
                    return tc, _render_tool_content(exec_result), list(exec_result.changed_files)
                except Exception as exc:  # noqa: BLE001
                    ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
                    return tc, f"[{tc.name} error] {exc}", []

            reg = ctx.kernel.tool_registry

            def _is_readonly(tc, reg=reg) -> bool:
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_readonly = len(result.tool_calls) >= 2 and all(_is_readonly(tc) for tc in result.tool_calls)
            if all_readonly:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_exec_one, result.tool_calls))
            else:
                outcomes = [_exec_one(tc) for tc in result.tool_calls]

            for tc, content, changed in outcomes:
                emit_event(ctx.event_callback, "tool_result", "Tool execution completed.", turn=turn, tool=tc.name)
                changed_files.extend(changed)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:_MAX_TOOL_RESULT_CHARS]})
        else:
            status = "failed"
            risks.append(f"loop did not finish within {max_turns} turn(s).")

        summary = write_report(
            workspace=ctx.original_workspace,
            run_id=ctx.run_id,
            task=ctx.task,
            plan=[f"loop paradigm: {turns_used} turn(s)."],
            changed_files=sorted(set(changed_files)),
            commands=[],
            verification="not_executed",
            risks=risks,
        )
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            turns=turns_used,
            changed_files=sorted(set(changed_files)),
            commands=[],
            verification="not_executed",
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks,
            mode=ctx.mode or "loop",
            answer=answer,
            transcript_path=transcript_rel,
        )


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
