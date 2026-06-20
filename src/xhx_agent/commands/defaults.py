"""默认命令注册 —— 使用新式 Command 对象，异步 handler 通过 CommandContext 访问 TUI。

已删除依赖旧栈的命令: /mode /evidence /repair /dashboard /live /context /diff /skills。
已由 handlers/ 覆盖的命令: /clear /help /plan /permission /compact /status /memory /session。
"""

from __future__ import annotations

from typing import Any

from xhx_agent.commands import Command, CommandContext

# ---------------------------------------------------------------------------
# handler 函数
# ---------------------------------------------------------------------------


async def _handle_exit(ctx: CommandContext) -> None:
    """退出 TUI（走与 ctrl+c 相同的优雅退出：清理 + 落盘 + 真正 exit）。"""
    if ctx.ui is not None:
        ctx.ui.add_system_message("正在退出...")
        graceful = getattr(ctx.ui, "graceful_exit", None)
        if graceful is not None:
            await graceful()


async def _handle_new(ctx: CommandContext) -> None:
    """新建会话。"""
    if ctx.ui is None:
        return
    clear_chat = ctx.config.get("clear_chat")
    set_session = ctx.config.get("set_session")
    set_conversation = ctx.config.get("set_conversation")
    if clear_chat:
        clear_chat()
    if set_session:
        set_session(None)
    if set_conversation:
        set_conversation(None)
    ctx.ui.add_system_message("已创建新会话")


async def _handle_allow(ctx: CommandContext) -> None:
    """提示如何批准权限请求。

    权限请求通过内联弹窗确认（弹窗出现时输入框被禁用，无法再输入命令），因此这里只能
    给出操作指引，而非声称"已批准"——后者是误导。
    """
    if ctx.ui is None:
        return
    ctx.ui.add_system_message(
        "权限请求请在弹出的确认框中用 ↑↓ 选择、回车确认；如需长期放行可用 /permission bypassPermissions"
    )


async def _handle_deny(ctx: CommandContext) -> None:
    """提示如何拒绝权限请求。"""
    if ctx.ui is None:
        return
    ctx.ui.add_system_message("权限请求请在弹出的确认框中选择拒绝项并回车")


async def _handle_model(ctx: CommandContext) -> None:
    """显示或切换模型。"""
    if ctx.ui is None:
        return
    if ctx.agent:
        profile = getattr(ctx.agent, "profile", None)
        provider = getattr(ctx.agent, "provider", None)
        if profile:
            ctx.ui.add_system_message(f"当前模型: {profile}")
        elif provider:
            ctx.ui.add_system_message(f"当前模型: {provider}")
        else:
            ctx.ui.add_system_message(f"当前模型: {getattr(ctx.agent, 'protocol', 'unknown')}")
    else:
        ctx.ui.add_system_message("Agent 未初始化")


async def _handle_cancel(ctx: CommandContext) -> None:
    """请求取消当前任务。

    正在执行的 agent 任务挂在 UI（_agent_task）上，不在 agent 对象上——之前查
    ctx.agent._agent_task 永远取不到，cancel 形同虚设。
    """
    if ctx.ui is None:
        return
    task = getattr(ctx.ui, "_agent_task", None)
    if task is not None and not task.done():
        task.cancel()
        ctx.ui.add_system_message("已请求取消当前任务")
    else:
        ctx.ui.add_system_message("当前没有正在执行的任务")


async def _handle_tools(ctx: CommandContext) -> None:
    """列出已注册的工具。"""
    if ctx.ui is None:
        return
    if ctx.agent is None:
        ctx.ui.add_system_message("Agent 未初始化")
        return
    # registry.list_tools() 返回的是 Tool 对象，不是名字字符串；is_enabled 收的是名字。
    names = [getattr(t, "name", str(t)) for t in ctx.agent.registry.list_tools()]
    enabled = [n for n in names if ctx.agent.registry.is_enabled(n)]
    lines = [f"工具: {len(enabled)} 个已启用 / {len(names)} 个已注册", ""]
    for n in names:
        flag = "✓" if n in enabled else "✗"
        lines.append(f"  {flag} {n}")
    ctx.ui.add_system_message("\n".join(lines))


async def _handle_verbose(ctx: CommandContext) -> None:
    """切换详细模式。"""
    if ctx.ui is None:
        return
    current = getattr(ctx.ui, "verbose", False)
    setattr(ctx.ui, "verbose", not current)  # noqa: B010  UIController 为 Protocol，verbose 是 TUI 动态属性
    state = "ON" if getattr(ctx.ui, "verbose", False) else "OFF"
    ctx.ui.add_system_message(f"Verbose: {state}")


# ---------------------------------------------------------------------------
# Command 对象
# ---------------------------------------------------------------------------

EXIT_CMD = Command(
    name="exit",
    description="退出 XHX-Agent",
    usage="/exit",
    handler=_handle_exit,
)

NEW_CMD = Command(
    name="new",
    description="新建会话（清空聊天与上下文）",
    usage="/new",
    handler=_handle_new,
)

ALLOW_CMD = Command(
    name="allow",
    description="批准待处理的权限请求",
    usage="/allow",
    handler=_handle_allow,
)

DENY_CMD = Command(
    name="deny",
    description="拒绝待处理的权限请求",
    usage="/deny",
    handler=_handle_deny,
)

MODEL_CMD = Command(
    name="model",
    description="显示当前模型信息",
    usage="/model",
    handler=_handle_model,
)

CANCEL_CMD = Command(
    name="cancel",
    description="取消当前正在执行的任务",
    usage="/cancel",
    handler=_handle_cancel,
)

TOOLS_CMD = Command(
    name="tools",
    description="列出已注册的工具",
    usage="/tools",
    handler=_handle_tools,
)

VERBOSE_CMD = Command(
    name="verbose",
    description="切换详细输出模式",
    usage="/verbose",
    handler=_handle_verbose,
)


# ---------------------------------------------------------------------------
# 注册入口
# ---------------------------------------------------------------------------


def register_default_commands(registry: Any) -> None:
    """注册所有默认命令（新式 Command 对象）。"""
    for cmd in [EXIT_CMD, NEW_CMD, ALLOW_CMD, DENY_CMD, MODEL_CMD, CANCEL_CMD, TOOLS_CMD, VERBOSE_CMD]:
        registry.register_sync(cmd)
