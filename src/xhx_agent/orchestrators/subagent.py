"""子 agent（dispatch）：父 loop/plan 把聚焦探索任务委派给隔离子循环。

子 agent 有**自己的消息历史**、**受限工具**（MVP 只读 explore=search/read_file）、**限定轮数**，跑完
只把浓缩结论回给父——父上下文只长一句。子 agent 的工具调用仍走 kernel（策略门控 + 证据 trace 不变）。
并行多子 agent / 写型 worktree 子 agent 留后续切片。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from xhx_agent.models import build_chat_client
from xhx_agent.models.routing import build_routed_client
from xhx_agent.runtime.events import emit_event

if TYPE_CHECKING:
    from xhx_agent.orchestrators.base import OrchestratorContext

# agent_type → 允许的（只读）工具集。MVP 仅 explore。
AGENT_TOOLSETS: dict[str, set[str]] = {
    "explore": {"search", "read_file"},
}
MAX_SUBAGENT_TURNS = 4
SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused sub-agent dispatched by a parent coding agent. Use ONLY the provided read-only "
    "tools to investigate, then reply with a concise, self-contained conclusion (a few sentences). "
    "Do not attempt to write files. Stop as soon as you can answer."
)


def run_subagent(
    ctx: OrchestratorContext,
    *,
    description: str,
    prompt: str,
    agent_type: str = "explore",
    turn: int = 0,
) -> str:
    """跑一个隔离只读子循环，返回浓缩结论（作为父的 dispatch 工具结果）。"""
    from xhx_agent.orchestrators._toolturn import _execute_tool_call_rich, chat_and_count

    allowed = AGENT_TOOLSETS.get(agent_type)
    if allowed is None:
        return f"[dispatch] unknown agent_type '{agent_type}'. Supported: {sorted(AGENT_TOOLSETS)}."

    client = build_routed_client(
        ctx.original_workspace,
        role="explore",
        base_profile_name=ctx.profile.name,
        event_callback=ctx.event_callback,
        build_client_func=build_chat_client,
    )
    schemas = [s for s in ctx.kernel.tool_registry.tool_schemas() if s["function"]["name"] in allowed]
    messages: list[dict] = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    emit_event(
        ctx.event_callback, "subagent_start", f"dispatch[{agent_type}]: {description or prompt[:60]}",
        turn=turn, agent_type=agent_type,
    )

    answer = ""
    for _ in range(MAX_SUBAGENT_TURNS):
        result = chat_and_count(ctx, client, messages, schemas)
        if not result.tool_calls:
            answer = result.content or ""
            break
        messages.append({
            "role": "assistant",
            "content": result.content or "",
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in result.tool_calls
            ],
        })
        for tc in result.tool_calls:
            if tc.name not in allowed:
                content = f"[dispatch] tool '{tc.name}' is not allowed for a '{agent_type}' sub-agent."
            else:
                _tc, content, _changed, _meta = _execute_tool_call_rich(ctx, tc, turn)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": content[:4000]})
    else:
        answer = answer or "Sub-agent reached its turn limit without a firm conclusion."

    emit_event(ctx.event_callback, "subagent_done", "Sub-agent finished.", turn=turn, agent_type=agent_type)
    return f"[sub-agent {agent_type}] {answer}".strip()
