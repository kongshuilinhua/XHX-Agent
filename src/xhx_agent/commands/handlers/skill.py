"""Skill 管理命令。"""
from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_skill(ctx: CommandContext) -> None:
    """列出或加载 Skill。"""
    skill_loader = ctx.config.get("skill_loader")
    if skill_loader is None:
        ctx.ui.add_system_message("Skill 加载器未初始化")
        return

    sub = ctx.args.strip() if ctx.args else "list"

    if sub == "list" or sub == "":
        skills = skill_loader.list_all() if hasattr(skill_loader, "list_all") else []
        if not skills:
            ctx.ui.add_system_message("暂无可用 Skill")
            return
        lines = ["可用 Skill："]
        for s in skills:
            name = getattr(s, "name", str(s))
            desc = getattr(s, "description", "") or ""
            lines.append(f"  {name:<20} {desc}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    ctx.ui.add_system_message("用法: /skill [list]")


SKILL_COMMAND = Command(
    name="skill",
    aliases=["sk"],
    description="列出可用 Skill",
    usage="/skill [list]",
    handler=handle_skill,
)
