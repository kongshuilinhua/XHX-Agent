"""代码审查命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_review(ctx: CommandContext) -> None:
    """触发代码审查（将审查提示注入对话）。"""
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    if ctx.conversation is None:
        ctx.ui.add_system_message("对话未初始化")
        return

    focus = ctx.args.strip() if ctx.args else "当前变更"
    prompt = (
        f"请审查以下代码变更，重点关注：{focus}。从正确性、安全性、性能、可维护性四个维度评估，列出发现的问题和建议。"
    )
    # 直接驱动 agent 跑一轮——之前只往历史里塞消息却不触发执行，得等用户再发一条才会动。
    ctx.ui.send_user_message(prompt)
    ctx.ui.add_system_message(f"已触发代码审查（聚焦: {focus}）")


REVIEW_COMMAND = Command(
    name="review",
    description="触发代码审查",
    usage="/review [关注点]",
    handler=handle_review,
)
