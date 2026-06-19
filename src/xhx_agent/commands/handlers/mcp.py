"""MCP 状态命令。"""

from __future__ import annotations

from xhx_agent.commands import Command, CommandContext


async def handle_mcp(ctx: CommandContext) -> None:
    """显示 MCP 服务器连接状态。"""
    # MCP 状态从 registry 中提取 mcp_ 前缀的工具
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return

    registry = ctx.agent.registry
    # list_tools() 返回 Tool 对象，取 .name 再判前缀（直接对对象 .startswith 会 AttributeError）。
    names = [getattr(t, "name", str(t)) for t in registry.list_tools()]
    mcp_tools = [n for n in names if n.startswith("mcp_")]
    if not mcp_tools:
        ctx.ui.add_system_message("未检测到 MCP 工具（可能未配置或连接失败）")
        return

    # 按 server 分组
    servers: dict[str, list[str]] = {}
    for t in mcp_tools:
        # 格式: mcp_<server>_<tool>
        parts = t.split("_", 2)
        server = parts[1] if len(parts) >= 2 else "unknown"
        servers.setdefault(server, []).append(t)

    lines = ["MCP 状态："]
    for server, stools in sorted(servers.items()):
        lines.append(f"  {server}: {len(stools)} 个工具")
        for t in sorted(stools):
            lines.append(f"    - {t}")
    ctx.ui.add_system_message("\n".join(lines))


MCP_COMMAND = Command(
    name="mcp",
    description="显示 MCP 服务器连接状态",
    usage="/mcp",
    handler=handle_mcp,
)
