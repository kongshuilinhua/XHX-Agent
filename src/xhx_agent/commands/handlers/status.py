"""状态显示命令。"""

from __future__ import annotations

import os

from xhx_agent import __version__
from xhx_agent.commands import Command, CommandContext


async def handle_status(ctx: CommandContext) -> None:
    """显示当前会话和系统状态。"""
    lines = ["XHX-Agent 状态", "──────────────"]

    # 权限模式
    if ctx.agent:
        mode = (
            ctx.agent.permission_mode.value
            if hasattr(ctx.agent.permission_mode, "value")
            else str(ctx.agent.permission_mode)
        )
        lines.append(f"模式: {mode}")
    else:
        lines.append("模式: 未知")

    # 会话信息：TUI 的 Session 用 session_id 标识（不是 run_id，那是 headless 侧的字段）。
    if ctx.session:
        lines.append(f"会话: {getattr(ctx.session, 'session_id', None) or '?'}")
    else:
        lines.append("会话: 无")

    # Token 用量：优先用对话的当前窗口占用估算（provider 不回传 usage 时 get_token_count 恒 0）。
    input_tokens, output_tokens = ctx.ui.get_token_count()
    used = input_tokens
    if ctx.conversation is not None:
        try:
            used = max(used, ctx.conversation.current_tokens())
        except Exception:
            pass
    context_window = ctx.agent.context_window if ctx.agent else 200_000
    pct = int(used / context_window * 100) if context_window else 0
    lines.append(f"Token: {used:,} / {context_window:,}（{pct}%）输出 {output_tokens:,}")

    # 工具
    if ctx.agent:
        # list_tools() 返回 Tool 对象；is_enabled 收名字字符串，需先取 .name。
        names = [getattr(t, "name", str(t)) for t in ctx.agent.registry.list_tools()]
        enabled = [n for n in names if ctx.agent.registry.is_enabled(n)]
        lines.append(f"工具: {len(enabled)} 个已启用 / {len(names)} 个已注册")

        # MCP：从 registry 与 manager 取实时状态（TUI 之外的 ctx.ui 没有这些属性则视为未配置）。
        mcp_tools = [n for n in names if n.startswith("mcp_")]
        manager = getattr(ctx.ui, "mcp_manager", None)
        failed = dict(getattr(manager, "failed_servers", None) or {})
        if getattr(ctx.ui, "_mcp_connecting", False):
            lines.append("MCP: 连接中…")
        elif mcp_tools or failed:
            server_count = len({n.split("_", 2)[1] for n in mcp_tools if len(n.split("_", 2)) >= 2})
            part = f"MCP: {server_count} 个 server / {len(mcp_tools)} 个工具"
            if failed:
                part += f"（{len(failed)} 个连接失败，详见 /mcp）"
            lines.append(part)
        elif getattr(ctx.ui, "_mcp_server_configs", None):
            lines.append("MCP: 已配置，未连接")
        else:
            lines.append("MCP: 未配置")

    # 记忆
    if ctx.memory_manager:
        content = ctx.memory_manager.load()
        mem_lines = [line for line in content.split("\n") if line.strip().startswith("- ")]
        lines.append(f"记忆: {len(mem_lines)} 条")

    # 工作目录
    work_dir = ctx.agent.work_dir if ctx.agent else os.getcwd()
    lines.append(f"工作目录: {work_dir}")
    lines.append(f"版本: {__version__}")

    ctx.ui.add_system_message("\n".join(lines))


STATUS_COMMAND = Command(
    name="status",
    description="显示当前状态信息",
    usage="/status",
    handler=handle_status,
)
