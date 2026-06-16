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


def _resolve_window(ctx: OrchestratorContext, client: Any) -> int:
    """解析本次调用模型的上下文窗口（profile.context_window > 模型名映射 > 缺省 128k）。"""
    from xhx_agent.runtime.profiles import resolve_context_window

    return resolve_context_window(getattr(ctx, "profile", None), getattr(client, "model", ""))


def chat_and_count(
    ctx: OrchestratorContext, client: Any, messages: list[dict], schemas: list[dict], turn: int = 0
) -> Any:
    """调 client.chat，累加 token 指标并 emit token_usage / context_pack 事件。

    - 估算路径（_estimate_message_tokens）保持不变：run_end 的 tokens_estimate 与回退仍可用。
    - 调用前先 emit 'context_pack'（used=本次将发送的估算 token，budget=模型窗口），让状态栏 Context
      在长调用期间也能显示用量、不再恒为 '—'；这是 loop/graph/plan 模式缺失的那一环（linear 走 app.py）。
    - 若 ChatResult 带 provider usage，则把真实 total 累加进 metrics_tracker['tokens_real']，emit
      'token_usage'（cumulative_total 供状态条），并用 API 真实 prompt token 再发一次 context_pack
      校正 Context 用量（对标 Claude：显示用 API usage 而非本地估算）。
    """
    import time

    window = _resolve_window(ctx, client)
    estimated = _estimate_message_tokens(messages)
    ctx.metrics_tracker["tokens"] = ctx.metrics_tracker.get("tokens", 0) + estimated
    emit_event(
        ctx.event_callback,
        "context_pack",
        "Context compiled.",
        turn=turn,
        budget_tokens=window,
        used_tokens_estimate=estimated,
    )
    t0 = time.perf_counter()
    result = client.chat(messages, schemas)
    duration_ms = int((time.perf_counter() - t0) * 1000)

    reasoning = getattr(result, "reasoning", None)
    if reasoning:
        emit_event(
            ctx.event_callback,
            "model_thinking",
            "Model reasoning.",
            turn=turn,
            model=getattr(client, "model", ""),
            text=reasoning,
        )

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
            turn=turn,
        )
        # 用 API 返回的真实 prompt token 校正 Context 用量（resident 输入=系统+历史+工具 schema）。
        if usage.prompt:
            emit_event(
                ctx.event_callback,
                "context_pack",
                "Context usage (provider).",
                turn=turn,
                budget_tokens=window,
                used_tokens_estimate=int(usage.prompt),
            )
    return result


def window_compact(
    ctx: OrchestratorContext, client: Any, messages: list[dict], summarize_fn, *, turn: int = 0
) -> list[dict]:
    """窗口感知压缩：超过 f(模型窗口) 阈值时把旧历史压成摘要，返回新 messages（否则原样）。

    供 graph/plan 在调用模型前调用——防长对话累积超过模型窗口被 provider 静默截断而「失忆」。
    summarize_fn 为空（如 mock 无 summarize）则跳过、零行为变更。
    """
    if not summarize_fn:
        return messages
    from xhx_agent.orchestrators.compaction import budget_for_window, compact_messages

    window = _resolve_window(ctx, client)
    threshold, keep_recent_tokens = budget_for_window(window)
    before = len(messages)
    out = compact_messages(messages, summarize_fn, max_tokens=threshold, keep_recent_tokens=keep_recent_tokens)
    if len(out) < before:
        emit_event(
            ctx.event_callback,
            "compaction",
            f"Compacted messages from {before} to {len(out)}.",
            turn=turn,
            before=before,
            after=len(out),
        )
    return out


def _execute_tool_call_rich(ctx: OrchestratorContext, tc, turn: int) -> tuple[Any, str, list[str], dict | None]:
    """同 execute_tool_call，但额外带回 meta（结构化工具成功时含 evidence_kind/source/summary/trace_id；否则 None）。"""
    emit_event(
        ctx.event_callback,
        "tool_start",
        f"Tool execution started: {tc.name}",
        turn=turn,
        tool=tc.name,
        arguments=tc.arguments,
    )
    status = "success"
    summary = ""
    try:
        if tc.name == "dispatch":
            from xhx_agent.orchestrators.subagent import WRITE_AGENT_TYPES, run_subagent, run_write_subagent

            agent_type = str(tc.arguments.get("agent_type") or "explore")
            description = str(tc.arguments.get("description", ""))
            prompt = str(tc.arguments.get("prompt", ""))

            # Team 模式：将子 agent 注册为团队成员
            team_mgr = getattr(ctx, "team_manager", None)
            team_name = getattr(ctx, "team_name", "")
            if team_mgr is not None and team_name:
                import uuid, time
                from xhx_agent.teams.models import TeammateInfo, BackendType
                from xhx_agent.teams.progress import TeammateProgress

                agent_id = f"agent-{uuid.uuid4().hex[:8]}"
                member_name = f"{agent_type}-{agent_id[:4]}"
                progress = TeammateProgress(name=member_name, team_name=team_name, status="running")
                member = TeammateInfo(
                    name=member_name, agent_id=agent_id, agent_type=agent_type,
                    model="", worktree_path=str(ctx.workspace),
                    backend_type=BackendType.IN_PROCESS.value, is_active=True,
                    progress=progress,
                )
                team_mgr.register_member(team_name, member)
                team_mgr.register_inprocess_handle(agent_id, None)
                # 存储 agent_id 以便完成后更新进度
                tc.arguments["_team_agent_id"] = agent_id
                tc.arguments["_team_name"] = team_name

            try:
                if agent_type in WRITE_AGENT_TYPES:
                    content, changed = run_write_subagent(ctx, description=description, prompt=prompt, turn=turn)
                else:
                    content = run_subagent(
                        ctx, description=description, prompt=prompt, agent_type=agent_type, turn=turn
                    )
                    changed = []
                # 标记 team 成员完成
                if team_mgr is not None and team_name:
                    agent_id = tc.arguments.get("_team_agent_id", "")
                    if agent_id:
                        team_mgr.set_member_idle(team_name, agent_id)
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
                cmd_result = ctx.kernel.run_command_tool(
                    command,
                    evidence_kind="test" if tc.name == "verify" else "command",
                    assume_yes=ctx.assume_yes,
                    confirm_callback=ctx.confirm_callback,
                    event_callback=ctx.event_callback,
                    turn=turn,
                )
                status = cmd_result.status
                summary = cmd_result.summary
                return tc, _render_tool_content(cmd_result), list(cmd_result.changed_files), None
            except Exception as exc:  # noqa: BLE001
                ctx.evidence.write_trace("tool_error", {"turn": turn, "tool": tc.name, "error": str(exc)})
                status = "error"
                summary = str(exc)
                return tc, f"[{tc.name} error] {exc}", [], None
        step = ToolStep(tool=tc.name, arguments=tc.arguments)
        try:
            exec_result, trace, policy = ctx.kernel.execute_tool(
                ctx.tool_context, step, turn, ctx.confirm_callback, ctx.event_callback, assume_yes=ctx.assume_yes
            )
            if exec_result is None:
                status = "denied"
                summary = policy.reason
                return tc, f"Tool denied/blocked: {policy.reason}", [], None
            meta = None
            if (
                trace is not None
                and exec_result.evidence_kind
                and exec_result.evidence_source
                and exec_result.evidence_summary
            ):
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
