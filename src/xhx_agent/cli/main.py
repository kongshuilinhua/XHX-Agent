from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Any

# Load .env file manually if exists in current working directory
env_path = Path.cwd() / ".env"
if env_path.is_file():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip("'").strip('"')
            os.environ.setdefault(k, v)

import typer
from rich.console import Console

from xhx_agent.repo_intel.index import diagnose_repo_intel_index, write_repo_intel_index
from xhx_agent.runtime.config import global_config_path, load_config, write_global_config
from xhx_agent.runtime.headless import HeadlessResult, run_headless_task
from xhx_agent.runtime.init import init_project
from xhx_agent.runtime.profiles import global_profiles_path, load_profiles, write_global_profiles
from xhx_agent.runtime.session import (
    SessionEntry,
    format_follow_up,
    load_latest_session,
    load_session,
    load_transcript_messages,
    record_session,
)
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tui.app import run_textual_console


def _ensure_utf8_console() -> None:
    """把 Windows 控制台切到 UTF-8，再构建 Rich Console。

    默认 Windows 控制台用 GBK/cp936，渲染状态行的 `•` 与时间线字形（⟶/✓/⚙/▸）会抛
    UnicodeEncodeError。在 Console() 构建前设好编码，避免控制台一启动就崩（与 cli/rpc.py 的
    reconfigure 同源）。非 Windows 直接跳过；所有调用都吞异常，绝不因环境差异而失败。
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


app = typer.Typer(help="xhx-agent local coding agent CLI.", invoke_without_command=True)
config_app = typer.Typer(help="Manage xhx-agent configuration.")
app.add_typer(config_app, name="config")
_ensure_utf8_console()
console = Console()


@app.callback(invoke_without_command=True)
def _default_callback(ctx: typer.Context) -> None:
    """无子命令时默认启动交互式 TUI；有子命令时放行，绝不抢先启动 TUI。"""
    if ctx.invoked_subcommand is not None:
        return
    workspace = Path.cwd()
    config = load_config(workspace)
    run_textual_console(workspace=workspace, profile=config.default_profile)


@app.command("tui")
def tui(
    fullscreen: Annotated[bool, typer.Option("--fullscreen", help="Deprecated: fullscreen is now the default.")] = True,
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
) -> None:
    """显式启动交互式 TUI（与无子命令时的默认行为一致）。"""
    workspace = Path.cwd()
    config = load_config(workspace)
    active_profile = profile or config.default_profile
    run_textual_console(workspace=workspace, profile=active_profile)


@app.command("init")
def init(
    global_: Annotated[
        bool,
        typer.Option(
            "--global",
            help="Write user-level config to ~/.xhx (config + profiles) so any directory can use it.",
        ),
    ] = False,
) -> None:
    if global_:
        config_created = write_global_config()
        profiles_created = write_global_profiles()
        console.print("Initialized xhx-agent global config (~/.xhx).")
        console.print(f"config.json: {'created' if config_created else 'exists'} ({global_config_path()})")
        console.print(f"profiles.json: {'created' if profiles_created else 'exists'} ({global_profiles_path()})")
        console.print(
            "Next: edit the 'default' profile in the global profiles.json with your real "
            "base_url/model/api_key_env, then export that API key. After that any directory "
            "falls back to this profile automatically."
        )
        return
    result = init_project(Path.cwd())
    console.print("Initialized xhx-agent project.")
    console.print(f"config.json: {'created' if result.config_created else 'exists'}")
    console.print(f"profiles.json: {'created' if result.profiles_created else 'exists'}")
    console.print(f"XHX.md: {'created' if result.xhx_md_created else 'exists'}")
    console.print(f"repo index: {result.repo_index_path}")


@app.command("repo-index")
def repo_index(
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON diagnostics.")] = False,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Rebuild .xhx/repo/index.json before printing diagnostics.")
    ] = False,
) -> None:
    if refresh:
        write_repo_intel_index(Path.cwd())
    diagnostics = diagnose_repo_intel_index(Path.cwd())
    if json_output:
        console.print(diagnostics.model_dump_json(indent=2))
        return
    console.print(f"repo index: {diagnostics.status}")
    console.print(f"path: {diagnostics.path}")
    console.print(f"reason: {diagnostics.reason}")
    if diagnostics.status in {"missing", "invalid"}:
        return
    console.print(f"schema: {diagnostics.schema_version}")
    console.print(f"size: {diagnostics.size_bytes} bytes")
    console.print(f"files: {diagnostics.file_count}")
    console.print(f"symbols: {diagnostics.symbol_count}")
    console.print(f"import edges: {diagnostics.import_edge_count}")
    console.print(f"call edges: {diagnostics.call_edge_count}")
    console.print(f"call graph truncated: {diagnostics.call_graph_truncated}")
    console.print(f"references: {diagnostics.reference_count}")
    console.print(f"reference truncated: {diagnostics.reference_truncated}")
    if diagnostics.skipped_reference_files:
        console.print("skipped reference files:")
        for path in diagnostics.skipped_reference_files[:10]:
            console.print(f"  - {path}")


def _record_run_session(workspace: Path, task: str, result: HeadlessResult) -> SessionEntry:
    """把一次 headless 运行落入会话索引，让 `xhx sessions` / --continue / --resume 仍可用。

    用一个最小的 duck-typed 适配对象对接 record_session，避免依赖旧栈的重型 RunResult。
    """
    import uuid
    from types import SimpleNamespace

    # run_id 与 trace 文件（.xhx/traces/<run_id>.jsonl）共用，`xhx replay` 才能直接回放。
    run_id = result.run_id or uuid.uuid4().hex[:12]
    runs_dir = workspace / ".xhx" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    summary_file = runs_dir / f"{run_id}.md"
    summary_text = result.summary or result.error or ""
    summary_file.write_text(summary_text, encoding="utf-8")

    adapter = SimpleNamespace(
        run_id=run_id,
        status=result.status,
        verification=result.verification,
        changed_files=list(result.changed_files or []),
        summary_path=summary_file.relative_to(workspace).as_posix(),
        transcript_path=None,
        mode="",
        # 完整对话 record：record_session 会落盘 .xhx/sessions/<run_id>.json 供 --resume 全量还原。
        messages=result.messages,
    )
    return record_session(workspace, task, adapter, turn_count=result.turns)  # type: ignore[arg-type]  # duck-typed 适配对象


def _restore_conversation(workspace: Path, entry: SessionEntry) -> Any:
    """按会话索引的 transcript 全量还原 ConversationManager；缺 transcript 返回 None（回退摘要续接）。"""
    records = load_transcript_messages(workspace, entry.transcript_path)
    if not records:
        return None
    from xhx_agent.conversation import ConversationManager
    from xhx_agent.runtime.session import records_to_messages

    messages = records_to_messages(records)
    if not messages:
        return None
    conversation = ConversationManager(history=messages)
    # 历史里已含上一轮注入的环境上下文/长期记忆，标记为已注入，续跑时不再重复插入。
    conversation.env_injected = True
    conversation.ltm_injected = True
    return conversation


@app.command("run")
def run(
    task: Annotated[str, typer.Argument(help="Task for xhx-agent to run.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON result.")] = False,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Allow confirm-level verification commands.")] = False,
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only build the first model plan; do not execute tools.")
    ] = False,
    auto_repair: Annotated[
        bool, typer.Option("--auto-repair", help="Allow up to two repair attempts after failed verification.")
    ] = False,
    cont: Annotated[
        bool,
        typer.Option("--continue", help="Resume from the most recent session, injecting its summary as context."),
    ] = False,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Resume from a specific session by run id (see `xhx sessions`)."),
    ] = None,
    mode: Annotated[
        str | None,
        typer.Option(
            "--mode",
            help="Orchestrator paradigm: loop | plan | graph (linear = auto-classify fallback; default: auto-classified).",
        ),
    ] = None,
    verify: Annotated[bool, typer.Option("--verify", help="Run change-targeted tests after the agent stops.")] = False,
) -> None:
    workspace = Path.cwd()
    # --mode / --auto-repair 是旧编排器/修复循环的概念，统一 Agent 循环下不再适用，仅作兼容接受。
    _ = (mode, auto_repair)
    if dry_run:
        console.print("--dry-run 在统一 Agent 循环下已不再支持，已跳过执行。")
        return

    effective_task = task
    prior_conversation = None
    if cont or resume:
        previous = load_latest_session(workspace) if cont else load_session(workspace, resume or "")
        if previous is not None:
            verb = "Continuing" if cont else "Resuming"
            # 优先全量还原完整对话历史；缺 transcript 的老会话回退摘要续接。
            prior_conversation = _restore_conversation(workspace, previous)
            if prior_conversation is not None:
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — full transcript restored.")
            else:
                effective_task = format_follow_up(previous) + "\n\n" + task
                console.print(f"{verb} from run {previous.run_id} ({previous.status}) — summary context injected.")
        else:
            target = "most recent session" if cont else f"session '{resume}'"
            console.print(f"No {target} found; starting fresh.")

    result = run_headless_task(
        workspace, effective_task, profile=profile, assume_yes=yes, verify=verify, conversation=prior_conversation
    )
    entry = _record_run_session(workspace, task, result)

    if json_output:
        console.print(
            json.dumps(
                {
                    "run_id": entry.run_id,
                    "status": result.status,
                    "summary": result.summary,
                    "error": result.error,
                    "verification": result.verification,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    console.print(f"status: {result.status}")
    if result.summary:
        console.print(f"summary: {result.summary}")
    if result.error:
        console.print(f"error: {result.error}")
    if result.verification:
        console.print(f"verification:\n{result.verification}")


@app.command("sessions")
def sessions() -> None:
    from rich.table import Table

    from xhx_agent.runtime.session import list_conversations

    workspace = Path.cwd().resolve()
    # One row per conversation (multi-turn console dialogues collapse to their latest entry).
    entries = list_conversations(workspace)
    if not entries:
        console.print("No sessions recorded yet.")
        return
    table = Table(title="Conversations")
    table.add_column("run_id")
    table.add_column("status")
    table.add_column("verification")
    table.add_column("task")
    for entry in list(reversed(entries))[:20]:
        table.add_row(entry.run_id, entry.status, entry.verification, entry.task[:60])
    console.print(table)


@app.command("chat")
def chat(
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
) -> None:
    """启动交互式 TUI 终端（默认入口）。"""
    workspace = Path.cwd()
    config = load_config(workspace)
    active_profile = profile or config.default_profile
    run_textual_console(workspace=workspace, profile=active_profile)


@config_app.command("list")
def config_list() -> None:
    workspace = Path.cwd()
    config = load_config(workspace)
    profiles = load_profiles(workspace)
    console.print("config:")
    console.print(config.model_dump_json(indent=2))
    console.print("profiles:")
    console.print(profiles.model_dump_json(indent=2))


@config_app.command("set-profile")
def config_set_profile(profile: str) -> None:
    # v0.1 keeps config mutation out of scope; this command validates intent.
    console.print(f"Profile switching is planned. Requested profile: {profile}")


def _confirm_terminal_command(command: str, decision: PolicyDecision) -> bool:
    console.print("Verification command requires confirmation.")
    console.print(f"command: {command}")
    console.print(f"risk: {decision.risk.value}")
    console.print(f"reason: {decision.reason}")
    try:
        return typer.confirm("Run this command?", default=False, abort=False)
    except (typer.Abort, EOFError, KeyboardInterrupt):
        console.print("Verification command declined.")
        return False


@app.command("rpc")
def rpc() -> None:
    from xhx_agent.cli.rpc import start_rpc_loop

    start_rpc_loop()


@app.command("replay")
def replay(
    run_id: Annotated[str, typer.Argument(help="Run ID to replay from trace file.")],
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON result.")] = False,
) -> None:
    from xhx_agent.evals.replay import TrailReplayer

    replayer = TrailReplayer(Path.cwd())
    try:
        result = replayer.replay(run_id)
        if json_output:
            console.print(result.model_dump_json(indent=2))
            return
        console.print(f"Replay successful for run: {run_id}")
        console.print(f"status: {result.status}")
        console.print(f"summary: {result.summary_path}")
        if result.metrics:
            console.print(f"turns: {result.metrics.turns}")
            console.print(f"duration: {result.metrics.duration_seconds}s")
            console.print(f"tokens: {result.metrics.tokens_estimate}")
    except Exception as e:
        console.print(f"[red]Error during replay: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("benchmark")
def benchmark(
    profile: Annotated[str, typer.Option("--profile", help="Model profile name to benchmark.")] = "mock",
    json_output: Annotated[bool, typer.Option("--json", help="Print structured JSON results.")] = False,
    modes: Annotated[
        str | None,
        typer.Option(
            "--modes", help="Comma-separated list of modes to benchmark: loop,plan,team (default: loop,plan,team)."
        ),
    ] = None,
) -> None:
    from xhx_agent.evals.benchmark import BenchmarkRunner

    runner = BenchmarkRunner(Path.cwd())
    if not json_output:
        console.print(f"Running benchmark fixtures against profile: {profile}...")
    try:
        if modes:
            import json

            from xhx_agent.evals.benchmark import render_benchmark_report

            mode_list = [m.strip() for m in modes.split(",") if m.strip()]
            report = render_benchmark_report(profile, runner.run_matrix(profile, mode_list))
            if json_output:
                print(json.dumps(report.model_dump(), ensure_ascii=False, indent=2))
                return
            out_dir = Path.cwd() / ".xhx" / "benchmark"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "report.md").write_text(report.markdown, encoding="utf-8")
            (out_dir / "report.json").write_text(
                json.dumps(report.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            console.print(report.markdown)
            console.print("\n[green]Report written to .xhx/benchmark/report.md | report.json[/green]")
            return
        results = runner.run_benchmark(profile)
        if json_output:
            import json

            print(json.dumps([r.model_dump() for r in results], indent=2))
            return

        # Render a beautiful rich table
        from rich.table import Table

        table = Table(title=f"xhx-agent Benchmark Results ({profile})")
        table.add_column("Fixture ID", style="cyan")
        table.add_column("Name", style="magenta")
        table.add_column("Status", style="green")
        table.add_column("Turns", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Est. Tokens", justify="right")
        table.add_column("Success", justify="center")

        for r in results:
            success_emoji = "✅" if r.success else "❌"
            status_style = "bold green" if r.status == "success" else "bold red"
            table.add_row(
                r.fixture_id,
                r.name,
                f"[{status_style}]{r.status}[/{status_style}]",
                str(r.turns),
                f"{r.duration_seconds:.2f}s",
                str(r.tokens_estimate),
                success_emoji,
            )
        console.print(table)
    except Exception as e:
        console.print(f"[red]Error running benchmarks: {e}[/red]")
        raise typer.Exit(code=1)


@app.command("memory")
def memory() -> None:
    from xhx_agent.memory import list_memories

    memories = list_memories(Path.cwd())
    if not memories:
        console.print("No memories recorded yet.")
        return
    from rich.table import Table

    table = Table(title="Memories")
    table.add_column("Name")
    table.add_column("Type")
    table.add_column("Description")
    for m in memories:
        table.add_row(m.name, m.mtype, m.description)
    console.print(table)


@app.command("compact")
def compact(
    profile: Annotated[str | None, typer.Option("--profile", help="Model profile name.")] = None,
    instructions: Annotated[
        str | None, typer.Option("--instructions", help="Focus/instructions for the summary.")
    ] = None,
) -> None:
    """Manually compact the message history transcript of the latest session."""
    workspace = Path.cwd().resolve()
    previous = load_latest_session(workspace)
    if not previous or not previous.transcript_path:
        console.print("[red]No recent session transcript found to compact.[/red]")
        return

    messages = load_transcript_messages(workspace, previous.transcript_path)
    if not messages:
        console.print("[red]Failed to load transcript messages.[/red]")
        return

    config = load_config(workspace)
    active_profile = profile or config.default_profile

    from xhx_agent.models import build_chat_client
    from xhx_agent.models.routing import resolve_profile_for_role
    from xhx_agent.runtime.profiles import get_profile

    try:
        prof = get_profile(workspace, active_profile)
        summarizer = build_chat_client(resolve_profile_for_role(workspace, "summarize", prof.name))
        summarize_fn = getattr(summarizer, "summarize", None)
    except Exception as e:
        console.print(f"[red]Failed to initialize summarizer: {e}[/red]")
        return

    if not summarize_fn:
        console.print("[red]Summarize client capability not found on active profile.[/red]")
        return

    from xhx_agent.context.compaction import compact_messages

    len_before = len(messages)
    try:
        compacted = compact_messages(
            messages,
            summarize_fn,
            force=True,
            custom_instructions=instructions,
        )
    except Exception as e:
        console.print(f"[red]Failed to run compaction: {e}[/red]")
        return

    len_after = len(compacted)
    if len_after < len_before:
        from xhx_agent.runtime.session import save_transcript

        save_transcript(workspace, previous.run_id, compacted)
        console.print(
            f"[green]Successfully compacted session {previous.run_id} from {len_before} to {len_after} messages.[/green]"
        )
    else:
        console.print("[yellow]No messages were compacted (already compacted or too short).[/yellow]")
