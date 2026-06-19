"""回退对话命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_rewind(ctx: CommandContext) -> None:
    """回退对话到指定轮数之前。"""
    if ctx.conversation is None:
        ctx.ui.add_system_message("对话未初始化")
        return

    try:
        n = int(ctx.args.strip()) if ctx.args.strip() else 1
    except ValueError:
        ctx.ui.add_system_message("用法: /rewind <轮数>，如 /rewind 3 回退 3 轮")
        return

    if n < 1:
        ctx.ui.add_system_message("轮回退数必须 >= 1")
        return

    history = ctx.conversation.history
    removed = 0
    for _ in range(n):
        if not history:
            break
        # 从尾部移除一轮（user + assistant + tool results）
        while history and getattr(history[-1], "role", "") not in ("user",):
            history.pop()
            removed += 1
        while history and getattr(history[-1], "role", "") == "user":
            history.pop()
            removed += 1

    # 重绘聊天区，让界面反映回退后的历史（否则被移除的消息仍显示在屏幕上）。
    render_restored = ctx.config.get("render_restored") if ctx.config else None
    if render_restored is not None:
        await render_restored(list(history))

    ctx.ui.add_system_message(f"已回退 {n} 轮（移除 {removed} 条消息）")


REWIND_COMMAND = Command(
    name="rewind",
    aliases=["rw"],
    description="回退对话 N 轮",
    usage="/rewind <轮数>",
    handler=handle_rewind,
)
