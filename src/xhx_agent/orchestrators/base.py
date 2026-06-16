"""可插拔编排器的抽象底座：定义 Orchestrator 协议与传给它的 OrchestratorContext。

双范式（loop / graph / linear / dag）都实现同一个 Orchestrator.run(ctx)，共用 ctx 里的
工具 / 安全内核 / 上下文 / 证据等基座——只有顶层控制流不同。编排器从不自己构造基座，全部从 ctx 取。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.models import build_chat_client  # noqa: F401  # module-level for test monkeypatch
from xhx_agent.models.routing import build_routed_client  # noqa: F401
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.runtime.events import EventCallback
from xhx_agent.runtime.profiles import ModelProfile
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tools.registry import ToolContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult, RuntimeApp

ConfirmationCallback = Callable[[str, PolicyDecision], bool]
CancelCheck = Callable[[], bool]


@dataclass
class PlanReview:
    decision: Literal["execute", "revise", "cancel"]
    feedback: str | None = None


# 当 git worktree 隔离不可用（非 git 仓库，或建 worktree 失败）而直接在用户工作区执行时抛出。
# 这种模式下失败的运行会把文件改动留在原地，没有自动的基线回滚。
IN_PLACE_WARNING = (
    "No git worktree isolation: changes were applied directly to the workspace and are NOT "
    "automatically rolled back on failure. Review the diff manually, or run inside a git "
    "repository for isolated execution."
)


@dataclass
class OrchestratorContext:
    """传给任意编排器的共享基座句柄 + 运行参数。

    RuntimeApp.run_task 先建好 worktree / evidence / kernel / scan / tool_context，
    打包进这里，再交给所选编排器的 run()。编排器从本 ctx 读取所需的一切，绝不自己构造基座。
    """

    app: RuntimeApp
    task: str
    run_id: str
    workspace: Path
    original_workspace: Path
    profile: ModelProfile
    scan: ProjectScan
    evidence: EvidenceStore
    kernel: SafeExecutionKernel
    tool_context: ToolContext
    start_time: float = field(default_factory=time.time)
    isolated: bool = True
    mode: str = ""
    autonomous: bool = False
    assume_yes: bool = False
    confirm_callback: ConfirmationCallback | None = None
    plan_review_callback: Callable[[str], PlanReview] | None = None
    auto_repair: bool = False
    cancel_check: CancelCheck | None = None
    event_callback: EventCallback | None = None
    metrics_tracker: dict[str, int] = field(default_factory=lambda: {"tokens": 0})
    prior_messages: list[dict] | None = None
    # 写型子 agent 串行合并用：rel_path → 最先改它的子 agent 标签；用于跨子 agent 的冲突检测（先到先得）。
    subagent_claims: dict[str, str] = field(default_factory=dict)
    # 并行写子 agent 用：串行化 git worktree 创建/清理 + _merge_into_parent（claims 与拷贝）的临界区。
    subagent_lock: threading.Lock = field(default_factory=threading.Lock)


class Orchestrator(Protocol):
    """基于共享基座的顶层控制流策略。

    各实现决定一个任务「怎么」被驱动（单一自主 loop vs 多 agent graph），
    同时通过 OrchestratorContext 复用同一套工具、安全内核、上下文编译器和证据存储。
    """

    name: str

    def run(self, ctx: OrchestratorContext) -> RunResult: ...


# ---------------------------------------------------------------------------
# BaseReActOrchestrator — loop / team 共享的 ReAct 循环
# ---------------------------------------------------------------------------


class BaseReActOrchestrator:
    """ReAct tool-use 统一循环的共享基类。

    LoopOrchestrator 和 TeamOrchestrator 继承此类，仅覆盖系统提示词、路由角色
    和 mode 标签，其余 ~200 行的 ReAct 循环逻辑全在此处。
    """

    name: str = "base"

    # ---- 子类覆盖点 ----

    def _system_prompt_content(self, ctx: OrchestratorContext) -> str:
        raise NotImplementedError

    def _role_name(self) -> str:
        raise NotImplementedError

    def _mode_name(self) -> str:
        return self.name

    def _before_run(self, ctx: OrchestratorContext, messages: list[dict]) -> None:
        """子类可在循环开始前注入额外上下文。"""
        pass

    def _verify_changes(
        self, ctx: OrchestratorContext, changed_files: list[str],
    ) -> tuple[str | None, list[str]]:
        """子类可覆盖：变更后自动验证。默认不做验证。

        Returns:
            (verification_status, commands_run) — status 可为 "passed"/"failed"/"skipped_no_changes"/None
        """
        if not changed_files:
            return ("skipped_no_changes", [])
        return (None, [])  # None → 调用方会用 "not_executed"

    # ---- 共享 ReAct 循环 ----

    def run(self, ctx: OrchestratorContext) -> "RunResult":
        import json
        import time
        from xhx_agent.evals.metrics import RunMetrics
        from xhx_agent.evidence.report import write_report
        from xhx_agent.memory.recall import render_recalled_memories
        from xhx_agent.models.routing import resolve_profile_for_role
        from xhx_agent.models.types import ModelClientError
        from xhx_agent.orchestrators._toolturn import _MAX_TOOL_RESULT_CHARS, chat_and_count, execute_tool_call
        from xhx_agent.orchestrators.compaction import budget_for_window, compact_messages
        from xhx_agent.repo_intel.xhx_md import render_xhx_md
        from xhx_agent.runtime.app import RunResult
        from xhx_agent.runtime.config import load_config
        from xhx_agent.runtime.events import emit_event
        from xhx_agent.runtime.profiles import resolve_context_window
        from xhx_agent.runtime.session import save_transcript

        client = build_routed_client(
            ctx.original_workspace,
            role=self._role_name(),
            base_profile_name=ctx.profile.name,
            event_callback=ctx.event_callback,
            build_client_func=build_chat_client,
        )
        if hasattr(client, "set_delta_callback"):
            client.set_delta_callback(lambda text: emit_event(ctx.event_callback, "model_delta", text))

        summarizer = build_chat_client(
            resolve_profile_for_role(ctx.original_workspace, "summarize", ctx.profile.name))
        summarize_fn = getattr(summarizer, "summarize", None)

        window = resolve_context_window(ctx.profile, getattr(client, "model", ""))
        compact_threshold, compact_keep_recent_tokens = budget_for_window(window)
        schemas = ctx.kernel.tool_registry.tool_schemas()

        messages: list[dict] = [{
            "role": "system",
            "content": self._system_prompt_content(ctx)
            + "\n\n" + render_xhx_md(ctx.scan)
            + render_recalled_memories(ctx.original_workspace, ctx.task),
        }]

        if ctx.prior_messages:
            messages.extend(m for m in ctx.prior_messages if m.get("role") != "system")

        self._before_run(ctx, messages)

        messages.append({"role": "user", "content": ctx.task})

        changed_files: list[str] = []
        risks: list[str] = []
        max_turns = load_config(ctx.original_workspace).max_loop_turns
        answer: str | None = None
        status = "success"
        turns_used = 0
        mode = self._mode_name()

        # 每次 run 开始前清空跨子 agent 冲突检测表（旧 graph 在每轮 execute 前清）。
        # 不清空会导致前几轮的 claims 永久阻止后续 dispatch 改同一文件。
        ctx.subagent_claims.clear()

        for turn in range(1, max_turns + 1):
            turns_used = turn
            if ctx.cancel_check and ctx.cancel_check():
                status = "cancelled"
                risks.append("Run cancelled before model call.")
                break

            if summarize_fn:
                len_before = len(messages)
                messages = compact_messages(
                    messages, summarize_fn,
                    max_tokens=compact_threshold,
                    keep_recent_tokens=compact_keep_recent_tokens,
                )
                len_after = len(messages)
                if len_after < len_before:
                    emit_event(ctx.event_callback, "compaction",
                               f"Compacted messages from {len_before} to {len_after}.",
                               turn=turn, before=len_before, after=len_after)

            try:
                result = chat_and_count(ctx, client, messages, schemas, turn=turn)
            except ModelClientError as exc:
                ctx.evidence.write_trace("model_error", {"turn": turn, **exc.to_trace_payload()})
                emit_event(ctx.event_callback, "model_error", exc.message, turn=turn, code=exc.code)
                status = "failed"
                risks.append(exc.message)
                break

            if not result.tool_calls:
                answer = result.content or ""
                messages.append({"role": "assistant", "content": answer})
                emit_event(ctx.event_callback, "model_plan",
                           f"{mode} answer [turn {turn}]",
                           turn=turn, step_count=0, status="done")
                break

            messages.append({
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                } for tc in result.tool_calls],
            })

            def _run(tc, turn=turn):
                return execute_tool_call(ctx, tc, turn)

            reg = ctx.kernel.tool_registry

            def _is_parallel_safe(tc, reg=reg) -> bool:
                if tc.name == "dispatch":
                    return str(tc.arguments.get("agent_type", "explore")) != "edit"
                d = reg.definition(tc.name)
                return d is not None and d.read_only

            all_parallel_safe = (
                len(result.tool_calls) >= 2
                and all(_is_parallel_safe(tc) for tc in result.tool_calls)
            )
            if all_parallel_safe:
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(result.tool_calls), 8)) as pool:
                    outcomes = list(pool.map(_run, result.tool_calls))
            else:
                outcomes = [_run(tc) for tc in result.tool_calls]

            for tc, content, changed in outcomes:
                changed_files.extend(changed)
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "content": content[:_MAX_TOOL_RESULT_CHARS],
                })
        else:
            status = "failed"
            risks.append(f"{mode} did not finish within {max_turns} turn(s).")

        # 子类可覆盖以在变更后自动验证（team 模式会用）
        verification_status, verification_cmds = self._verify_changes(ctx, changed_files)
        if verification_cmds:
            commands_run = verification_cmds
        else:
            commands_run = []
            verification_status = verification_status or "not_executed"

        summary = write_report(
            workspace=ctx.original_workspace, run_id=ctx.run_id, task=ctx.task,
            plan=[f"{mode} paradigm: {turns_used} turn(s)."],
            changed_files=sorted(set(changed_files)), commands=commands_run,
            verification=verification_status, risks=risks,
        )
        transcript_rel = save_transcript(ctx.original_workspace, ctx.run_id, messages)
        ctx.evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        metrics = RunMetrics(
            duration_seconds=round(time.time() - ctx.start_time, 2),
            turns=turns_used,
            tokens_estimate=ctx.metrics_tracker.get("tokens", 0),
            files_changed_count=len(set(changed_files)),
            commands_run_count=len(commands_run), repair_attempts=0,
            success=(status == "success"),
        )
        return RunResult(
            run_id=ctx.run_id, status=status, turns=turns_used,
            changed_files=sorted(set(changed_files)), commands=commands_run,
            verification=verification_status,
            summary_path=str(summary.relative_to(ctx.original_workspace)),
            risk_summary=risks, mode=mode, answer=answer,
            transcript_path=transcript_rel, metrics=metrics,
        )
