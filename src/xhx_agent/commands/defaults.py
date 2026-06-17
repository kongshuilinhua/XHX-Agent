"""默认命令注册——handler 直接调用 TUI app 的实际方法。"""

from __future__ import annotations

from typing import Any

from xhx_agent.commands.registry import CommandRegistry


def register_default_commands(registry: CommandRegistry) -> None:
    r = registry
    r.register("/exit", "Exit the textual command console", _exit)
    r.register("/clear", "Clear conversation messages and details", _clear)
    r.register("/new", "Clear conversation and start a new session", _new)
    r.register("/allow", "Approve the pending permission confirmation", _allow)
    r.register("/deny", "Deny the pending permission confirmation", _deny)
    r.register("/help", "Show help message and command details", _help)
    r.register("/model", "List or switch active profile", _model, needs_arg=True, arg_hint="[profile_name]")
    r.register("/plan", "Show active plan or preview a task plan", _plan, needs_arg=True, arg_hint="[task]")
    r.register("/mode", "Show or set orchestrator execution mode", _mode, needs_arg=True, arg_hint="[loop|plan|team]")
    r.register("/context", "Show current context pack budget & files", _context)
    r.register("/evidence", "Show recent safety policy evidence", _evidence)
    r.register("/perm", "Show or set permission mode", _perm, needs_arg=True, arg_hint="[default|acceptEdits|bypass]")
    r.register("/diff", "Show git diff summary for changes", _diff)
    r.register("/repair", "Repair codebase after failed verification", _repair, needs_arg=True, arg_hint="[loop|auto]")
    r.register("/skills", "List available skill directories", _skills)
    r.register("/dashboard", "Print detailed dashboard runtime state", _dashboard)
    r.register("/live", "Toggle live streaming dashboard render", _live)
    r.register("/cancel", "Request task cancellation at safe boundary", _cancel)
    r.register("/sessions", "List recent recorded agent sessions", _sessions, needs_arg=True, arg_hint="[keyword|clear]")
    r.register("/resume", "Switch follow-up context to a past session", _resume, needs_arg=True, arg_hint="<run_id_prefix>")
    r.register("/tools", "Show details of recent tool calls", _tools)
    r.register("/verbose", "Toggle verbose inline tool call details", _verbose)


# ---- handlers below: each receives (app, argument) and calls TUI methods ----

def _exit(app: Any, _arg: str) -> bool:
    app.exit_requested = True
    return False

def _clear(app: Any, _arg: str) -> bool:
    app.action_clear()
    return True

def _new(app: Any, _arg: str) -> bool:
    app.action_clear()
    return True

def _allow(app: Any, _arg: str) -> bool:
    if not app.resolve_pending_confirmation(True) and not app.resolve_pending_plan_review("execute"):
        app.next_confirm_response = True
        app.append_message("system> next permission prompt will be allowed once")
    return True

def _deny(app: Any, _arg: str) -> bool:
    if not app.resolve_pending_confirmation(False) and not app.resolve_pending_plan_review("cancel"):
        app.next_confirm_response = False
        app.append_message("system> next permission prompt will be declined once")
    return True

def _help(app: Any, _arg: str) -> bool:
    app.append_message("system> available commands:\n" + app._build_help_text())
    return True

def _model(app: Any, arg: str) -> bool:
    app.print_model(arg or None)
    return True

def _plan(app: Any, arg: str) -> bool:
    app.print_plan_preview(arg or None)
    return True

def _mode(app: Any, arg: str) -> bool:
    app.set_mode(arg)
    return True

def _context(app: Any, _arg: str) -> bool:
    app.print_context_summary()
    return True

def _evidence(app: Any, _arg: str) -> bool:
    app.print_evidence_summary()
    return True

def _perm(app: Any, arg: str) -> bool:
    from xhx_agent.safety.permission_mode import permission_mode_from_string, permission_mode_title
    if arg:
        new_mode = permission_mode_from_string(arg)
        app.state.permission_mode = new_mode
        app.append_message(f"system> 权限模式: {permission_mode_title(new_mode)}")
        app.set_detail("perm", f"权限模式: {permission_mode_title(new_mode)}")
    else:
        app.append_message(
            f"system> 当前权限模式: {permission_mode_title(app.state.permission_mode)} ({app.state.permission_mode})"
        )
    app.refresh_snapshot()
    return True

def _diff(app: Any, _arg: str) -> bool:
    app.print_diff_summary()
    return True

def _repair(app: Any, arg: str) -> bool:
    max_attempts = 2 if arg.lower() in {"loop", "auto"} else 1
    if getattr(app, '_slash_use_worker', False) and getattr(app, 'widgets_ready', False):
        app.start_manual_repair_worker(max_attempts=max_attempts)
    else:
        app.run_manual_repair(max_attempts=max_attempts)
    return True

def _skills(app: Any, _arg: str) -> bool:
    app.print_skills()
    return True

def _dashboard(app: Any, _arg: str) -> bool:
    app.print_dashboard_summary()
    return True

def _live(app: Any, _arg: str) -> bool:
    app.append_message("system> live: rich-only in v0.5 fullscreen; Textual already refreshes its fixed panels")
    return True

def _cancel(app: Any, _arg: str) -> bool:
    app.request_cancel()
    return True

def _sessions(app: Any, arg: str) -> bool:
    if arg.strip() == "clear":
        from xhx_agent.runtime.session import prune_legacy_sessions
        n = prune_legacy_sessions(app.workspace)
        app.append_message(f"system> 已清理 {n} 条旧会话")
        app.refresh_snapshot()
    else:
        app.handle_sessions(arg)
    return True

def _resume(app: Any, arg: str) -> bool:
    app.handle_resume(arg)
    return True

def _tools(app: Any, _arg: str) -> bool:
    app.handle_tools()
    return True

def _verbose(app: Any, _arg: str) -> bool:
    """切换详细模式（对标 Claude Code --verbose）。持久化到项目级 config。"""
    current = getattr(app, "verbose", False)
    app.verbose = not current
    state = "on" if app.verbose else "off"
    app.append_message(f"system> verbose: {state}")

    # 持久化到 .xhx/config.json（对标 Claude Code AppState.verbose → saveGlobalConfig）
    try:
        from xhx_agent.runtime.config import load_config, save_config
        cfg = load_config(app.workspace)
        cfg.verbose = app.verbose
        save_config(app.workspace, cfg)
        app.append_message("system> verbose saved to .xhx/config.json")
    except Exception:
        pass
    return True


