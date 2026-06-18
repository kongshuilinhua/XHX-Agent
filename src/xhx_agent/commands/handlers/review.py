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
        f"请审查以下代码变更，重点关注：{focus}。"
        "从正确性、安全性、性能、可维护性四个维度评估，列出发现的问题和建议。"
    )
    ctx.conversation.add_user_message(prompt)
    ctx.ui.add_system_message(f"已注入审查提示（聚焦: {focus}），Agent 将在下一轮回复中给出审查结果")


REVIEW_COMMAND = Command(
    name="review",
    aliases=["rv"],
    description="触发代码审查",
    usage="/review [关注点]",
    arg_prompt="可选：指定审查关注点（如 安全性、性能）",
    handler=handle_review,
)
