"""上下文压缩命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_compact(ctx: CommandContext) -> None:
    """手动触发上下文压缩。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    # 用对话的当前窗口占用估算，而非 get_token_count——provider 不回传 usage 时后者恒 0，
    # 会让 /compact 永远误判"无需压缩"。
    used, _ = ctx.ui.get_token_count()
    if ctx.conversation is not None:
        try:
            used = max(used, ctx.conversation.current_tokens())
        except Exception:
            pass
    if used < 5000:
        ctx.ui.add_system_message(f"当前 token 数约 {used:,}，无需压缩")
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
