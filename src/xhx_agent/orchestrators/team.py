"""Team Orchestrator：Coordinator 模式——Leader 调度 Team 成员完成任务。

基于 LoopOrchestrator 的 ReAct 循环，注入 Coordinator 系统提示词。
Leader 通过 dispatch 工具按需调度 worker Agent。
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.memory.recall import render_recalled_memories
from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client, resolve_profile_for_role
from xhx_agent.models.types import ModelClientError
from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, chat_and_count, execute_tool_call
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.orchestrators.compaction import budget_for_window, compact_messages
from xhx_agent.repo_intel.xhx_md import render_xhx_md
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import emit_event
from xhx_agent.runtime.session import save_transcript

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult

TEAM_SYSTEM_PROMPT = """\
You are xhx-agent in TEAM (Coordinator) mode. You are the LEAD agent coordinating a team of workers.

## Your Role
- Help the user achieve their goal by directing workers to research, implement, and verify code changes
- Synthesize results and communicate with the user
- Answer questions directly when possible — don't delegate work you can handle without tools

## Your Tools
- **dispatch** — Spawn a worker agent (agent_type: "explore" for read-only search, "general-purpose" for full capability)
- All standard tools (search, read_file, apply_patch, terminal) — use them directly for simple single-file tasks
- If a task requires investigating multiple files or making coordinated changes across files, use dispatch

## Worker Guidelines
- Give workers clear, self-contained prompts with specific deliverables
- Don't use one worker to check another — workers notify you when done
- After launching workers, briefly tell the user what you launched and end your response — don't predict results
- Workers return concise conclusions — relay key findings to the user

## Conversation Style
Default to replying directly in natural language. ONLY use tools when the request genuinely requires it.
Use relative paths only. All writes go through apply_patch.
"""


class TeamOrchestrator:
    """Coordinator 模式编排器。

    Leader Agent 注入 Coordinator 系统提示词，以 ReAct 循环运行。
    Worker 通过 dispatch 工具并行/串行调度。
    """

    name = "team"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        from xhx_agent.evidence.report import write_report
        from xhx_agent.runtime.app import RunResult

        # 加载 Agent 目录给 Leader 参考
        from xhx_agent.agents.loader import AgentLoader
        from xhx_agent.teams.coordinator import get_coordinator_system_prompt

        loader = AgentLoader(str(ctx.original_workspace))
        loader.load_all()
        catalog = loader.list_agents()
        coordinator_prompt = get_coordinator_system_prompt(catalog)

        client = build_routed_client(
            ctx.original_workspace,
            role="team",
            base_profile_name=ctx.profile.name,
            event_callback=ctx.event_callback,
            build_client_func=build_chat_client,
        )
        if hasattr(client, "set_delta_callback"):
            client.set_delta_callback(lambda text: emit_event(ctx.event_callback, "model_delta", text))

        summarizer = build_chat_client(
            resolve_profile_for_role(ctx.original_workspace, "summarize", ctx.profile.name)
        )
        summarize_fn = getattr(summarizer, "summarize", None)

        from xhx_agent.runtime.profiles import resolve_context_window
        window = resolve_context_window(ctx.profile, getattr(client, "model", ""))
        compact_threshold, compact_keep_recent_tokens = budget_for_window(window)

        schemas = ctx.kernel.tool_registry.tool_schemas()
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": TEAM_SYSTEM_PROMPT + "\n\n" + coordinator_prompt + "\n\n"
                + render_xhx_md(ctx.scan)
                + render_recalled_memories(ctx.original_workspace, ctx.task),
            },
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
            if summarize_fn:
                len_before = len(messages)
                messages = compact_messages(
                    messages,
                    summarize_fn,
                    max_tokens=compact_threshold,
                    keep_recent_tokens=compact_keep_recent_tokens,
                )
                len_after = len(messages)
                if len_after < len_before:
                    emit_event(
                        ctx.event_callback,
                        "compaction",
                        f"Compacted messages from {len_before} to {len_after}.",
                        turn=turn, before=len_before, after=len_after,
                    )
            try:
                result = chat_and_count(ctx, client, messages, schemas, turn=turn)
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
                    ctx.event_callback, "model_plan",
                    f"team answer [turn {turn}]",
                    turn=turn, step_count=0, status="done",
                )
                break

            messages.append({
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
            })

            def _run(tc, turn=turn):
                return execute_tool_call(ctx, tc, turn)

            reg = ctx.kernel.tool_registry

            def _is_parallel_safe(tc, reg=reg) -> bool:
                if tc.name == "dispatch":
                    return str(tc.arguments.get("agent_type", "explore")) != "edit"
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_parallel_safe = (
                len(result.tool_calls) >= 2
                and all(_is_parallel_safe(tc) for tc in result.tool_calls)
            )
            if all_parallel_safe:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_run, result.tool_calls))
            else:
                outcomes = [_run(tc) for tc in result.tool_calls]

            for tc, content, changed in outcomes:
                changed_files.extend(changed)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content[:_MAX_TOOL_RESULT_CHARS],
                })
        else:
            status = "failed"
            risks.append(f"team did not finish within {max_turns} turn(s).")

        summary = write_report(
            workspace=ctx.original_workspace,
            run_id=ctx.run_id,
            task=ctx.task,
            plan=[f"team paradigm: {turns_used} turn(s)."],
            changed_files=sorted(set(changed_files)),
            commands=[],
            verification="not_executed",
            risks=risks,
        )
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        metrics = RunMetrics(
            duration_seconds=round(time.time() - ctx.start_time, 2),
            turns=turns_used,
            tokens_estimate=ctx.metrics_tracker.get("tokens", 0),
            files_changed_count=len(set(changed_files)),
            commands_run_count=0,
            repair_attempts=0,
            success=(status == "success"),
        )
        return RunResult(
            run_id=ctx.run_id,
            status=status,
            turns=turns_used,
            changed_files=sorted(set(changed_files)),
            commands=[],
            verification="not_executed",
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks,
            mode="team",
            answer=answer,
            transcript_path=transcript_rel,
            metrics=metrics,
        )
