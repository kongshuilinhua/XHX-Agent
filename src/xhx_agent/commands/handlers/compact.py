"""上下文压缩命令。"""
from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_compact(ctx: CommandContext) -> None:
    """手动触发上下文压缩。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    input_tokens, _ = ctx.ui.get_token_count()
    if input_tokens < 5000:
        ctx.ui.add_system_message(f"当前 token 数 {input_tokens:,}，无需压缩")
        return

    result = await ctx.agent.manual_compact(ctx.conversation)
    from xhx_agent.agents.agent_runner import CompactNotification

    if isinstance(result, CompactNotification):
        ctx.ui.add_system_message(result.message)
    else:
        ctx.ui.add_system_message(f"压缩失败: {result}")


COMPACT_COMMAND = Command(
    name="compact",
    aliases=["c"],
    description="压缩上下文（token 超阈值时用 LLM 摘要压缩历史）",
    usage="/compact",
    handler=handle_compact,
)
