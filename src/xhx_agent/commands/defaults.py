"""默认命令注册：所有内置斜杠命令。

从 tui/textual_app.py 的 handle_slash_command 提取，改为声明式注册。
"""

from __future__ import annotations

from typing import Any

from xhx_agent.commands.registry import CommandRegistry


def register_default_commands(registry: CommandRegistry) -> None:
    """注册所有内置斜杠命令。"""

    # ── 信息类 ──
    registry.register("/help", "Show help message and command details", _cmd_help)
    registry.register("/status", "Show current agent status", _cmd_status)
    registry.register("/tools", "Show available tools", _cmd_tools)

    # ── 模型/配置类 ──
    registry.register("/model", "List or switch active profile", _cmd_model, needs_arg=True, arg_hint="[profile_name]")
    registry.register("/mode", "Show or set orchestrator execution mode", _cmd_mode, needs_arg=True, arg_hint="[loop|plan|team]")

    # ── 会话类 ──
    registry.register("/sessions", "List recent sessions", _cmd_sessions)
    registry.register("/resume", "Resume a previous session", _cmd_resume, needs_arg=True, arg_hint="<run_id>")

    # ── 操作类 ──
    registry.register("/clear", "Clear conversation display", _cmd_clear)
    registry.register("/new", "Start a new conversation", _cmd_new)
    registry.register("/cancel", "Cancel the current running task", _cmd_cancel)

    # ── 权限类 ──
    registry.register("/allow", "Approve the pending confirmation", _cmd_allow)
    registry.register("/deny", "Deny the pending confirmation", _cmd_deny)
    registry.register("/perm", "Show or set permission mode", _cmd_perm, needs_arg=True, arg_hint="[default|acceptEdits|bypass]")

    # ── 上下文/证据类 ──
    registry.register("/context", "Show context pack usage", _cmd_context)
    registry.register("/evidence", "Show evidence index for current run", _cmd_evidence)
    registry.register("/diff", "Show working tree diff", _cmd_diff)

    # ── 验证/修复类 ──
    registry.register("/verify", "Run verification on changed files", _cmd_verify)
    registry.register("/repair", "Restore files from checkpoint", _cmd_repair, needs_arg=True, arg_hint="<checkpoint_id>")

    # ── 其他 ──
    registry.register("/skills", "List loaded skills", _cmd_skills)
    registry.register("/dashboard", "Show live TUI dashboard", _cmd_dashboard)
    registry.register("/live", "Toggle live streaming display", _cmd_live)
    registry.register("/verbose", "Toggle verbose mode", _cmd_verbose)
    registry.register("/exit", "Exit the textual command console", _cmd_exit)


# ---------------------------------------------------------------------------
# 命令处理函数（从 TUI 提取）
# ---------------------------------------------------------------------------


def _cmd_help(_app: Any, _arg: str) -> str:
    lines = [
        "/help      - Show help message and command details",
        "/model     - List or switch active profile",
        "/mode      - Show or set orchestrator execution mode (loop/plan/team)",
        "/status    - Show current agent status",
        "/context   - Show context pack usage",
        "/evidence  - Show evidence index for current run",
        "/diff      - Show working tree diff",
        "/verify    - Run verification on changed files",
        "/repair    - Restore files from checkpoint",
        "/skills    - List loaded skills",
        "/dashboard - Show live TUI dashboard",
        "/live      - Toggle live streaming display",
        "/cancel    - Cancel the current running task",
        "/clear     - Clear conversation display",
        "/new       - Start a new conversation",
        "/perm      - Show or set permission mode",
        "/sessions  - List recent sessions",
        "/resume    - Resume a previous session",
        "/allow     - Approve the pending confirmation",
        "/deny     - Deny the pending confirmation",
        "/tools     - Show available tools",
        "/verbose   - Toggle verbose mode",
        "/exit      - Exit the textual command console",
    ]
    return "\n".join(lines)


def _cmd_status(app: Any, _arg: str) -> str | None:
    if hasattr(app, 'render_status_line'):
        app.render_status_line()
    return None


def _cmd_tools(app: Any, _arg: str) -> str:
    if hasattr(app, 'kernel') and app.kernel:
        schemas = app.kernel.tool_registry.tool_schemas()
        lines = [f"{s['name']}: {s.get('description', '')[:80]}" for s in schemas]
        return "\n".join(lines) or "No tools registered."
    return "Kernel not available."


def _cmd_model(app: Any, arg: str) -> str | None:
    if hasattr(app, '_handle_model_command'):
        app._handle_model_command(arg)
    return None


def _cmd_mode(app: Any, arg: str) -> str | None:
    if hasattr(app, '_handle_mode_command'):
        app._handle_mode_command(arg)
    return None


def _cmd_sessions(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_show_sessions'):
        app._show_sessions()
    return None


def _cmd_resume(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_handle_resume_command'):
        app._handle_resume_command(_arg)
    return None


def _cmd_clear(app: Any, _arg: str) -> bool | None:
    if hasattr(app, 'clear_messages'):
        app.clear_messages()
    return True


def _cmd_new(app: Any, _arg: str) -> bool | None:
    if hasattr(app, 'new_conversation'):
        app.new_conversation()
    return True


def _cmd_cancel(app: Any, _arg: str) -> bool | None:
    if hasattr(app, 'cancel_current_task'):
        app.cancel_current_task()
    return True


def _cmd_allow(app: Any, _arg: str) -> bool | None:
    if hasattr(app, 'handle_confirm_allow'):
        app.handle_confirm_allow()
    return True


def _cmd_deny(app: Any, _arg: str) -> bool | None:
    if hasattr(app, 'handle_confirm_deny'):
        app.handle_confirm_deny()
    return True


def _cmd_perm(app: Any, arg: str) -> str | None:
    if hasattr(app, '_handle_perm_command'):
        app._handle_perm_command(arg)
    return None


def _cmd_context(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_show_context'):
        app._show_context()
    return None


def _cmd_evidence(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_show_evidence'):
        app._show_evidence()
    return None


def _cmd_diff(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_show_diff'):
        app._show_diff()
    return None


def _cmd_verify(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_run_verification'):
        app._run_verification()
    return None


def _cmd_repair(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_handle_repair_command'):
        app._handle_repair_command(_arg)
    return None


def _cmd_skills(app: Any, _arg: str) -> str:
    if hasattr(app, 'skill_loader'):
        skills = app.skill_loader.load_available_skills()
        if not skills:
            return "No skills loaded."
        lines = [f"{s.name}: {s.description}" for s in skills]
        return "\n".join(lines)
    return "Skill loader not available."


def _cmd_dashboard(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_show_dashboard'):
        app._show_dashboard()
    return None


def _cmd_live(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_toggle_live'):
        app._toggle_live()
    return None


def _cmd_verbose(app: Any, _arg: str) -> str | None:
    if hasattr(app, '_toggle_verbose'):
        app._toggle_verbose()
    return None


def _cmd_exit(_app: Any, _arg: str) -> bool:
    return False
