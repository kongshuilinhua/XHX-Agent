"""Team Orchestrator：Coordinator 模式——Leader 调度 Team 成员完成任务。

替代 graph 编排器。基于 LoopOrchestrator 的 ReAct 循环，注入 Coordinator 系统提示词，
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
- **dispatch** — Spawn a worker agent (agent_type: "explore" for read-only search, "general-purpose" for full tasks)
- All standard tools (search, read_file, apply_patch, terminal, verify) — use them directly for simple tasks

## Worker Guidelines
- Use dispatch for complex multi-step investigations or changes that would clutter your context
- Give workers clear, self-contained prompts with specific deliverables
- Don't use one worker to check another — workers notify you when done
- After launching workers, briefly tell the user and end your response — don't predict results
- Workers return concise conclusions — relay key findings to the user

## Verification
- Any code change should be verified independently — use a separate dispatch call for verification
- Or run verify tool directly for test suites
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

        summarizer = build_chat_client(resolve_profile_for_role(ctx.original_workspace, "summarize", ctx.profile.name))
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
            messages.extend(ctx.prior_messages)
        messages.append({"role": "user", "content": ctx.task})

        config = load_config(ctx.original_workspace)
        max_turns = config.max_loop_turns
        tool_summaries: list[str] = []
        total_tokens = 0
        t0 = time.monotonic()

        for turn in range(1, max_turns + 1):
            if ctx.cancel_check and ctx.cancel_check():
                return RunResult(status="cancelled", task=ctx.task, run_id=ctx.run_id,
                                 changed_files=[], tool_calls=[], tokens=total_tokens,
                                 metrics=RunMetrics(), elapsed_seconds=time.monotonic() - t0,
                                 mode="team")

            emit_event(ctx.event_callback, "orchestrator_turn", f"Turn {turn}/{max_turns}", turn=turn)
            messages = compact_messages(
                messages, summarize_fn, compact_threshold,
                keep_recent_tokens=compact_keep_recent_tokens,
            )

            try:
                chat_result = chat_and_count(ctx, client, messages, schemas, turn)
            except ModelClientError as e:
                return RunResult(status="failed", task=ctx.task, run_id=ctx.run_id,
                                 changed_files=[], tool_calls=[],
                                 tokens=total_tokens, error_message=str(e),
                                 metrics=RunMetrics(), elapsed_seconds=time.monotonic() - t0,
                                 mode="team")

            if chat_result is None:
                break

            total_tokens += chat_result.token_usage.get("total_tokens", 0)
            content = chat_result.content or ""

            if chat_result.tool_calls:
                messages.append({"role": "assistant", "content": content, "tool_calls": chat_result.raw_tool_calls})
                for tc in chat_result.tool_calls:
                    _, result_content, changed_files = execute_tool_call(ctx, tc, turn)
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_content[:10000]})
            else:
                messages.append({"role": "assistant", "content": content})
                break

        try:
            save_transcript(ctx.run_id, messages, ctx.original_workspace)
        except Exception:
            pass

        metrics = RunMetrics()
        return RunResult(
            status="success", task=ctx.task, run_id=ctx.run_id,
            changed_files=[], tool_calls=[], tokens=total_tokens,
            metrics=metrics, elapsed_seconds=time.monotonic() - t0,
            mode="team",
        )
