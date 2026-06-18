"""帮助命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_help(ctx: CommandContext) -> None:
    """显示命令帮助。"""
    registry = ctx.config["registry"]

    if ctx.args:
        cmd = registry.find(ctx.args.lower())
        if cmd is None:
            ctx.ui.add_system_message(f"未知命令：{ctx.args}，输入 /help 查看可用命令")
            return
        lines = [f"/{cmd.name}"]
        if cmd.aliases:
            lines[0] += f"  (别名: {', '.join('/' + a for a in cmd.aliases)})"
        lines.append(f"  {cmd.description}")
        if cmd.usage:
            lines.append(f"  用法: {cmd.usage}")
        if cmd.arg_prompt:
            lines.append(f"  参数: {cmd.arg_prompt}")
        ctx.ui.add_system_message("\n".join(lines))
        return

    commands = registry.list_commands()
    lines = ["可用命令："]
    for cmd in commands:
        aliases = getattr(cmd, "aliases", []) or []
        aliases_str = ", ".join(f"/{a}" for a in aliases)
        name_part = f"/{cmd.name}"
        if aliases_str:
            name_part += f", {aliases_str}"
        lines.append(f"  {name_part:<28} {cmd.description}")
    lines.append("")
    lines.append("输入 /help <命令名> 查看详细用法。")
    ctx.ui.add_system_message("\n".join(lines))


HELP_COMMAND = Command(
    name="help",
    aliases=["h", "?"],
    description="显示帮助信息",
    usage="/help [命令名]",
    handler=handle_help,
)
