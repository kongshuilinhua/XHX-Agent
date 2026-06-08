from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import BaseModel

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.context.debug import write_context_debug_report
from xhx_agent.context.pack import ContextPack
from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.evidence.store import EvidenceEntry, EvidenceStore
from xhx_agent.models.mock import MockModelClient
from xhx_agent.models.openai_compatible import OpenAICompatibleClient
from xhx_agent.models.types import ModelClientError, ModelPlan
from xhx_agent.planner.classifier import ModeClassifier
from xhx_agent.planner.modes import ExecutionMode
from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.xhx_md import write_xhx_md
from xhx_agent.runtime.config import load_config, write_default_config
from xhx_agent.runtime.dag_runner import DAGRunner
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.runtime.git_ops import DiffSummary, GitOps
from xhx_agent.runtime.paths import ensure_xhx_dirs
from xhx_agent.runtime.profiles import ModelProfile, get_profile, write_default_profiles
from xhx_agent.runtime.utils import cancel_requested, new_run_id
from xhx_agent.runtime.verify_loop import (
    ManualRepairResult,
    ManualVerificationResult,
    VerificationLoop,
    VerificationLoopContext,
    _last_verification_error,
    _refresh_repo_intel_index,
    checkpoint_path_value,
    restore_plan_path_value,
)
from xhx_agent.safety.checkpoint import Checkpoint
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.repair import MAX_REPAIR_ATTEMPTS, RepairDecision, decide_repair
from xhx_agent.safety.worktree import WorktreeContext
from xhx_agent.skills.hooks import hooks_manager
from xhx_agent.tools.registry import ToolContext, ToolRegistry, default_tool_registry
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.verification.router import infer_verification

# Surfaced when a run executes directly in the user's workspace because git worktree
# isolation was unavailable (not a git repo, or worktree creation failed). In that mode a
# failed run leaves its file changes in place; there is no automatic baseline rollback.
_IN_PLACE_WARNING = (
    "No git worktree isolation: changes were applied directly to the workspace and are NOT "
    "automatically rolled back on failure. Review the diff manually, or run inside a git "
    "repository for isolated execution."
)


class InitResult(BaseModel):
    config_created: bool
    profiles_created: bool
    xhx_md_created: bool
    repo_index_path: str


class RunResult(BaseModel):
    run_id: str
    status: str
    turns: int = 0
    changed_files: list[str]
    commands: list[str]
    verification: str
    verification_results: list[TerminalResult] = []
    checkpoint_path: str | None = None
    restore_plan_path: str | None = None
    repair: RepairDecision | None = None
    repair_attempts: int = 0
    summary_path: str
    risk_summary: list[str]
    metrics: RunMetrics | None = None
    mode: str = ""


class PlanPreview(BaseModel):
    run_id: str
    status: str
    summary: str
    step_count: int
    context_budget_tokens: int
    context_used_tokens_estimate: int
    trace_path: str
    risk_summary: list[str]


ConfirmationCallback = Callable[[str, PolicyDecision], bool]
CancelCheck = Callable[[], bool]


class RuntimeApp:
    def __init__(self, workspace: Path | None = None, tool_registry: ToolRegistry | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.tool_registry = tool_registry or default_tool_registry()

    def init_project(self) -> InitResult:
        ensure_xhx_dirs(self.workspace)
        config_created = write_default_config(self.workspace)
        profiles_created = write_default_profiles(self.workspace)
        scan = scan_project(self.workspace)
        xhx_md_created = write_xhx_md(self.workspace, scan)
        repo_index = write_repo_intel_index(self.workspace)
        return InitResult(
            config_created=config_created,
            profiles_created=profiles_created,
            xhx_md_created=xhx_md_created,
            repo_index_path=repo_index.relative_to(self.workspace).as_posix(),
        )

    def run_task(
        self,
        task: str,
        profile_name: str | None = None,
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        auto_repair: bool = False,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> RunResult:
        start_time = time.time()
        metrics_tracker = {"tokens": 0}
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        run_id = new_run_id("run")

        original_workspace = self.workspace.resolve()
        with WorktreeContext(original_workspace, run_id) as wt_ctx:
            try:
                self.workspace = wt_ctx.active_path

                evidence = EvidenceStore(original_workspace, run_id)
                kernel = SafeExecutionKernel(self.workspace, run_id, evidence, self.tool_registry)
                emit_event(event_callback, "run_start", "Run started.", run_id=run_id, task=task, profile=profile.name)
                evidence.write_trace("run_start", {"task": task, "profile": profile.name})
                if not wt_ctx.is_active:
                    evidence.write_trace("isolation_degraded", {"reason": "no_git_worktree"})
                    emit_event(
                        event_callback,
                        "isolation_degraded",
                        "Running in place without git worktree isolation; failed changes are not auto-reverted.",
                        run_id=run_id,
                    )
                scan = scan_project(self.workspace)
                emit_event(
                    event_callback,
                    "scan",
                    "Project scan completed.",
                    detected_languages=scan.detected_languages,
                    file_count=scan.file_count,
                )
                changed_files: list[str] = []
                commands_run: list[str] = []
                verification_results: list[TerminalResult] = []
                checkpoint: Checkpoint | None = None
                restore_plan_created = False
                repair_decision: RepairDecision | None = None
                repair_attempts = 0
                risks: list[str] = []
                status = "success"
                turns_completed = 0
                plan_summaries: list[str] = [
                    "Load project configuration.",
                    f"Scan project languages: {', '.join(scan.detected_languages) or 'unknown'}.",
                ]
                tool_summaries: list[str] = []
                evidence_entries: list[EvidenceEntry] = []
                recent_error: str | None = None
                tool_context = ToolContext(workspace=self.workspace, max_file_bytes=config.max_file_bytes)

                if cancel_requested(cancel_check):
                    status = "cancelled"
                    verification_status = "cancelled"
                    risks.append("Run cancelled by user before model planning.")
                    evidence.write_trace("cancel_requested", {"stage": "before_model_loop"})
                    emit_event(event_callback, "run_cancelled", "Run cancelled before model planning.", run_id=run_id)
                    summary = write_report(
                        workspace=original_workspace,
                        run_id=run_id,
                        task=task,
                        plan=plan_summaries + ["Run cancelled before model planning."],
                        changed_files=[],
                        commands=[],
                        verification=verification_status,
                        risks=risks,
                    )
                    evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
                    emit_event(
                        event_callback,
                        "run_end",
                        "Run cancelled.",
                        run_id=run_id,
                        status=status,
                        verification=verification_status,
                        changed_files=[],
                        summary_path=str(summary.relative_to(original_workspace)),
                    )
                    metrics = RunMetrics(
                        duration_seconds=round(time.time() - start_time, 2),
                        turns=turns_completed,
                        tokens_estimate=metrics_tracker["tokens"],
                        files_changed_count=0,
                        commands_run_count=0,
                        repair_attempts=repair_attempts,
                        success=False,
                    )
                    return RunResult(
                        run_id=run_id,
                        status=status,
                        turns=turns_completed,
                        changed_files=[],
                        commands=[],
                        verification=verification_status,
                        verification_results=[],
                        summary_path=str(summary.relative_to(original_workspace)),
                        risk_summary=risks,
                        metrics=metrics,
                        mode="direct",
                    )

                mode = ModeClassifier().classify(task, scan)

                if mode == ExecutionMode.DAG_EXECUTE:
                    runner = DAGRunner(self)
                    res = runner.run_dag(
                        task=task,
                        run_id=run_id,
                        evidence=evidence,
                        kernel=kernel,
                        tool_context=tool_context,
                        assume_yes=assume_yes,
                        confirm_callback=confirm_callback,
                        event_callback=event_callback,
                        cancel_check=cancel_check,
                        start_time=start_time,
                        metrics_tracker=metrics_tracker,
                    )
                    res.mode = "dag-execute"
                    if res.status == "success":
                        wt_ctx.sync_to_workspace(res.changed_files)
                    elif not wt_ctx.is_active and res.changed_files:
                        res.risk_summary.append(_IN_PLACE_WARNING)
                    return res

                # Linear Edit Mode
                status, turns_completed, recent_error = self._run_model_tool_loop(
                    task=task,
                    profile=profile,
                    scan=scan,
                    evidence=evidence,
                    kernel=kernel,
                    tool_context=tool_context,
                    changed_files=changed_files,
                    tool_summaries=tool_summaries,
                    evidence_entries=evidence_entries,
                    plan_summaries=plan_summaries,
                    risks=risks,
                    recent_error=recent_error,
                    starting_turn=1,
                    event_callback=event_callback,
                    cancel_check=cancel_check,
                    metrics_tracker=metrics_tracker,
                )
                if changed_files and status not in {"failed", "cancelled"}:
                    _refresh_repo_intel_index(self.workspace, evidence, event_callback, risks)

                verification_plan = infer_verification(self.workspace, changed_files) if changed_files else None
                commands = [item.command for item in verification_plan.commands] if verification_plan else []
                verification_status = (
                    "cancelled"
                    if status == "cancelled"
                    else "not_executed"
                    if status == "failed"
                    else (verification_plan.skip_reason or "not_executed")
                    if verification_plan
                    else "skipped_no_changes"
                )
                if status not in {"failed", "cancelled"} and not changed_files:
                    verification_status = "skipped_no_changes"
                    evidence.write_trace("verification_skipped", {"reason": "No changed files."})

                loop_ctx = VerificationLoopContext(
                    task=task,
                    run_id=run_id,
                    profile=profile,
                    scan=scan,
                    evidence=evidence,
                    kernel=kernel,
                    tool_context=tool_context,
                    metrics_tracker=metrics_tracker,
                    assume_yes=assume_yes,
                    confirm_callback=confirm_callback,
                    auto_repair=auto_repair,
                    cancel_check=cancel_check,
                    event_callback=event_callback,
                    status=status,
                    verification_status=verification_status,
                    changed_files=changed_files,
                    commands_run=commands_run,
                    verification_results=verification_results,
                    repair_attempts=repair_attempts,
                    turns_completed=turns_completed,
                    recent_error=recent_error,
                    tool_summaries=tool_summaries,
                    evidence_entries=evidence_entries,
                    plan_summaries=plan_summaries,
                    risks=risks,
                )
                (
                    status,
                    verification_status,
                    repair_attempts,
                    turns_completed,
                    recent_error,
                    checkpoint,
                    repair_decision,
                    restore_plan_created,
                ) = self._execute_verification_and_repair_loop(loop_ctx)

                if status == "success":
                    wt_ctx.sync_to_workspace(changed_files)
                elif not wt_ctx.is_active and changed_files:
                    risks.append(_IN_PLACE_WARNING)

                with contextlib.suppress(Exception):
                    hooks_manager.trigger(
                        "before_summary",
                        workspace=self.workspace,
                        run_id=run_id,
                        task=task,
                        status=status,
                        changed_files=changed_files,
                    )

                summary = write_report(
                    workspace=original_workspace,
                    run_id=run_id,
                    task=task,
                    plan=plan_summaries + ["Write run summary."],
                    changed_files=sorted(set(changed_files)),
                    commands=commands_run or commands,
                    verification=verification_status,
                    risks=risks,
                    verification_results=verification_results,
                    checkpoint_path=str(checkpoint_path_value(original_workspace, run_id)) if checkpoint else None,
                    restore_plan_path=str(restore_plan_path_value(original_workspace, run_id))
                    if restore_plan_created
                    else None,
                    repair=repair_decision,
                    repair_attempts=repair_attempts,
                )
                evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
                emit_event(
                    event_callback,
                    "run_end",
                    "Run finished.",
                    run_id=run_id,
                    status=status,
                    verification=verification_status,
                    changed_files=sorted(set(changed_files)),
                    summary_path=str(summary.relative_to(original_workspace)),
                )
                metrics = RunMetrics(
                    duration_seconds=round(time.time() - start_time, 2),
                    turns=turns_completed,
                    tokens_estimate=metrics_tracker["tokens"],
                    files_changed_count=len(changed_files),
                    commands_run_count=len(commands_run or commands),
                    repair_attempts=repair_attempts,
                    success=(status == "success"),
                )
                return RunResult(
                    run_id=run_id,
                    status=status,
                    turns=turns_completed,
                    changed_files=sorted(set(changed_files)),
                    commands=commands_run or commands,
                    verification=verification_status,
                    verification_results=verification_results,
                    checkpoint_path=str(checkpoint_path_value(original_workspace, run_id)) if checkpoint else None,
                    restore_plan_path=str(restore_plan_path_value(original_workspace, run_id))
                    if restore_plan_created
                    else None,
                    repair=repair_decision,
                    repair_attempts=repair_attempts,
                    summary_path=str(summary.relative_to(original_workspace)),
                    risk_summary=risks,
                    metrics=metrics,
                    mode=mode.value,
                )
            finally:
                self.workspace = original_workspace

    def _execute_verification_and_repair_loop(
        self,
        ctx: VerificationLoopContext,
    ) -> tuple[str, str, int, int, str | None, Checkpoint | None, RepairDecision | None, bool]:
        checkpoint: Checkpoint | None = None
        restore_plan_created = False
        repair_decision: RepairDecision | None = None
        verify_loop = VerificationLoop(self)

        while ctx.status not in {"failed", "cancelled"} and ctx.changed_files:
            if cancel_requested(ctx.cancel_check):
                ctx.status = "cancelled"
                ctx.verification_status = "cancelled"
                ctx.risks.append("Run cancelled by user before verification.")
                ctx.evidence.write_trace("cancel_requested", {"stage": "before_verification"})
                emit_event(ctx.event_callback, "run_cancelled", "Run cancelled before verification.", run_id=ctx.run_id)
                break

            verification_plan = infer_verification(self.workspace, ctx.changed_files)
            commands = [item.command for item in verification_plan.commands]
            checkpoint = ctx.kernel.create_checkpoint(sorted(set(ctx.changed_files)))
            emit_event(
                ctx.event_callback,
                "checkpoint",
                "Checkpoint created.",
                checkpoint_id=checkpoint.id,
                changed_files=sorted(set(ctx.changed_files)),
            )
            ctx.verification_results.clear()
            ctx.commands_run.clear()

            ctx.status, ctx.verification_status = verify_loop.execute_verification_loop(
                kernel=ctx.kernel,
                commands=commands,
                assume_yes=ctx.assume_yes,
                confirm_callback=ctx.confirm_callback,
                event_callback=ctx.event_callback,
                cancel_check=ctx.cancel_check,
                run_id=ctx.run_id,
                evidence=ctx.evidence,
                risks=ctx.risks,
                commands_run_accumulated=ctx.commands_run,
                results_accumulated=ctx.verification_results,
            )
            if ctx.status == "cancelled":
                break

            with contextlib.suppress(Exception):
                hooks_manager.trigger("after_verify", workspace=self.workspace, results=ctx.verification_results)

            from xhx_agent.planner.agents import ReviewerAgent

            reviewer = ReviewerAgent()
            review_dec = reviewer.review(ctx.task, ctx.changed_files, ctx.verification_results)
            ctx.evidence.write_trace("reviewer_decision", review_dec.model_dump())

            repair_decision = decide_repair(
                ctx.verification_status, attempts_used=ctx.repair_attempts, auto_repair_enabled=ctx.auto_repair
            )
            ctx.evidence.write_trace("repair_decision", repair_decision.model_dump())
            emit_event(
                ctx.event_callback,
                "repair_decision",
                repair_decision.reason,
                should_repair=repair_decision.should_repair,
                attempts_used=ctx.repair_attempts,
                max_attempts=repair_decision.max_attempts,
            )
            if ctx.verification_status != "failed":
                break
            if not repair_decision.should_repair:
                ctx.evidence.write_evidence(
                    "error",
                    "repair",
                    repair_decision.reason,
                    f"trace://{ctx.run_id}/repair_decision",
                    confidence=0.8,
                )
                ctx.risks.append(f"Repair not attempted: {repair_decision.reason}")
                break

            ctx.repair_attempts += 1
            emit_event(
                ctx.event_callback,
                "repair_start",
                "Repair attempt started.",
                attempt=ctx.repair_attempts,
                max_attempts=MAX_REPAIR_ATTEMPTS,
            )
            ctx.evidence.write_evidence(
                "decision",
                "repair",
                f"Repair attempt {ctx.repair_attempts}/{MAX_REPAIR_ATTEMPTS}: {repair_decision.reason}",
                f"trace://{ctx.run_id}/repair/{ctx.repair_attempts}",
                confidence=0.7,
            )
            ctx.recent_error = _last_verification_error(ctx.verification_results)
            ctx.tool_summaries.append(f"verification failed: {ctx.recent_error}")
            before_repair_changed = len(ctx.changed_files)
            ctx.status = "success"

            from xhx_agent.planner.agents import CoderAgent

            coder = CoderAgent(self)
            ctx.status, ctx.turns_completed, ctx.recent_error = coder.execute_turn(
                task=f"Repair after failed verification: {ctx.task}",
                profile=ctx.profile,
                scan=ctx.scan,
                evidence=ctx.evidence,
                kernel=ctx.kernel,
                tool_context=ctx.tool_context,
                changed_files=ctx.changed_files,
                tool_summaries=ctx.tool_summaries,
                evidence_entries=ctx.evidence_entries,
                plan_summaries=ctx.plan_summaries,
                risks=ctx.risks,
                recent_error=ctx.recent_error,
                turn=ctx.turns_completed + 1,
                event_callback=ctx.event_callback,
                cancel_check=ctx.cancel_check,
                metrics_tracker=ctx.metrics_tracker,
            )
            if ctx.status == "cancelled" or ctx.status == "failed":
                break
            if len(ctx.changed_files) == before_repair_changed:
                ctx.status = "failed"
                message = "Repair loop produced no additional changes."
                ctx.risks.append(message)
                ctx.evidence.write_trace(
                    "repair_decision", {"should_repair": False, "reason": message, "attempts_used": ctx.repair_attempts}
                )
                break
            _refresh_repo_intel_index(self.workspace, ctx.evidence, ctx.event_callback, ctx.risks)

        if ctx.status == "failed" and checkpoint is not None:
            ctx.kernel.create_restore_plan(checkpoint)
            restore_plan_created = True
            emit_event(ctx.event_callback, "restore_plan", "Restore plan created.", run_id=ctx.run_id)

        return (
            ctx.status,
            ctx.verification_status,
            ctx.repair_attempts,
            ctx.turns_completed,
            ctx.recent_error,
            checkpoint,
            repair_decision,
            restore_plan_created,
        )

    def run_task_json(
        self,
        task: str,
        profile_name: str | None = None,
        assume_yes: bool = False,
        auto_repair: bool = False,
    ) -> str:
        return json.dumps(
            self.run_task(task, profile_name, assume_yes=assume_yes, auto_repair=auto_repair).model_dump(),
            ensure_ascii=False,
            indent=2,
        )

    def preview_plan(self, task: str, profile_name: str | None = None) -> PlanPreview:
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        run_id = new_run_id("dry-run")
        evidence = EvidenceStore(self.workspace, run_id)
        evidence.write_trace("run_start", {"task": task, "profile": profile.name, "dry_run": True})
        scan = scan_project(self.workspace)
        context_pack = compile_context_pack(workspace=self.workspace, task=task, scan=scan)
        context_debug = write_context_debug_report(self.workspace, run_id, 1, context_pack)
        evidence.write_trace("context_pack", context_pack.model_dump())
        evidence.write_trace(
            "context_debug_report", {"turn": 1, "path": str(context_debug.relative_to(self.workspace))}
        )
        risks: list[str] = []
        try:
            plan = self._build_plan(task, profile, context_pack)
            evidence.write_trace("model_plan_preview", plan.model_dump())
            self.tool_registry.validate_plan(plan)
            summary = plan.summary
            status = "success"
            step_count = len(plan.steps)
        except ModelClientError as exc:
            evidence.write_trace("model_error", exc.to_trace_payload())
            risks.append(exc.message)
            summary = exc.message
            status = "failed"
            step_count = 0
        evidence.write_trace("run_end", {"status": status, "dry_run": True})
        return PlanPreview(
            run_id=run_id,
            status=status,
            summary=summary,
            step_count=step_count,
            context_budget_tokens=context_pack.budget_tokens,
            context_used_tokens_estimate=context_pack.used_tokens_estimate,
            trace_path=str(evidence.trace_path.relative_to(self.workspace)),
            risk_summary=risks,
        )

    def diff_changed_files(self, changed_files: list[str], max_chars: int = 12_000) -> DiffSummary:
        return GitOps(self.workspace).diff_changed_files(changed_files, max_chars)

    def verify_changed_files(
        self,
        changed_files: list[str],
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ManualVerificationResult:
        return VerificationLoop(self).verify_changed_files(
            changed_files=changed_files,
            assume_yes=assume_yes,
            confirm_callback=confirm_callback,
            event_callback=event_callback,
            cancel_check=cancel_check,
        )

    def repair_after_failed_verification(
        self,
        task: str,
        failed_verification_results: list[TerminalResult],
        changed_files: list[str],
        profile_name: str | None = None,
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        max_attempts: int = 1,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ManualRepairResult:
        return VerificationLoop(self).repair_after_failed_verification(
            task=task,
            failed_verification_results=failed_verification_results,
            changed_files=changed_files,
            profile_name=profile_name,
            assume_yes=assume_yes,
            confirm_callback=confirm_callback,
            max_attempts=max_attempts,
            event_callback=event_callback,
            cancel_check=cancel_check,
        )

    def _execute_verification_loop(
        self,
        *,
        kernel: SafeExecutionKernel,
        commands: list[str],
        assume_yes: bool,
        confirm_callback: ConfirmationCallback | None,
        event_callback: EventCallback | None,
        cancel_check: CancelCheck | None,
        run_id: str,
        evidence: EvidenceStore,
        risks: list[str],
        commands_run_accumulated: list[str],
        results_accumulated: list[TerminalResult],
        manual: bool = False,
    ) -> tuple[str, str]:
        return VerificationLoop(self).execute_verification_loop(
            kernel=kernel,
            commands=commands,
            assume_yes=assume_yes,
            confirm_callback=confirm_callback,
            event_callback=event_callback,
            cancel_check=cancel_check,
            run_id=run_id,
            evidence=evidence,
            risks=risks,
            commands_run_accumulated=commands_run_accumulated,
            results_accumulated=results_accumulated,
            manual=manual,
        )

    def _build_plan(
        self,
        task: str,
        profile: ModelProfile,
        context_pack: ContextPack,
        event_callback: EventCallback | None = None,
        turn: int | None = None,
    ) -> ModelPlan:
        if profile.provider == "mock":
            return MockModelClient().plan(task, self.workspace)
        if profile.provider == "openai-compatible":

            def emit_model_delta(delta: str) -> None:
                emit_event(
                    event_callback,
                    "model_delta",
                    delta,
                    turn=turn,
                    profile=profile.name,
                    length=len(delta),
                )

            return OpenAICompatibleClient(
                base_url=profile.base_url,
                api_key_env=profile.api_key_env,
                model=profile.model,
                temperature=profile.temperature,
                stream=profile.stream,
            ).plan(task, context_pack, delta_callback=emit_model_delta if profile.stream else None)
        raise ModelClientError(
            code="unsupported_provider",
            message=f"Unsupported model provider: {profile.provider}",
            details={"provider": profile.provider},
        )

    def _build_plan_for_turn(
        self,
        task: str,
        profile: ModelProfile,
        context_pack: ContextPack,
        event_callback: EventCallback | None,
        turn: int,
    ) -> ModelPlan:
        return self._build_plan(
            task,
            profile,
            context_pack,
            event_callback=event_callback,
            turn=turn,
        )

    def _run_model_tool_loop(
        self,
        *,
        task: str,
        profile: ModelProfile,
        scan,
        evidence: EvidenceStore,
        kernel: SafeExecutionKernel,
        tool_context: ToolContext,
        changed_files: list[str],
        tool_summaries: list[str],
        evidence_entries: list[EvidenceEntry],
        plan_summaries: list[str],
        risks: list[str],
        recent_error: str | None,
        starting_turn: int,
        max_turns: int | None = None,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
        metrics_tracker: dict[str, int] | None = None,
    ) -> tuple[str, int, str | None]:
        status = "success"
        turns_completed = starting_turn - 1
        turn_limit = max_turns or _max_model_turns(profile)
        for offset in range(turn_limit):
            turn = starting_turn + offset
            if cancel_requested(cancel_check):
                message = "Run cancelled by user before context compilation."
                risks.append(message)
                evidence.write_trace("cancel_requested", {"stage": "before_context_pack", "turn": turn})
                emit_event(event_callback, "run_cancelled", message, run_id=evidence.run_id, turn=turn)
                return "cancelled", turns_completed, message
            context_pack = compile_context_pack(
                workspace=self.workspace,
                task=task,
                scan=scan,
                changed_files=sorted(set(changed_files)),
                tool_summaries=tool_summaries,
                plan_summaries=plan_summaries,
                evidence_entries=evidence_entries,
                recent_error=recent_error,
            )
            context_debug = write_context_debug_report(self.workspace, evidence.run_id, turn, context_pack)
            evidence.write_trace("context_pack", {"turn": turn, **context_pack.model_dump()})
            evidence.write_trace(
                "context_debug_report", {"turn": turn, "path": str(context_debug.relative_to(self.workspace))}
            )
            if metrics_tracker is not None:
                metrics_tracker["tokens"] += context_pack.used_tokens_estimate
            emit_event(
                event_callback,
                "context_pack",
                "Context compiled.",
                turn=turn,
                budget_tokens=context_pack.budget_tokens,
                used_tokens_estimate=context_pack.used_tokens_estimate,
            )
            if cancel_requested(cancel_check):
                message = "Run cancelled by user before model planning."
                risks.append(message)
                evidence.write_trace("cancel_requested", {"stage": "before_model_plan", "turn": turn})
                emit_event(event_callback, "run_cancelled", message, run_id=evidence.run_id, turn=turn)
                return "cancelled", turns_completed, message
            try:
                hooks_manager.trigger("before_plan", task=task, turn=turn, profile=profile, context_pack=context_pack)
            except Exception:
                pass
            try:
                from xhx_agent.planner.agents import PlannerAgent

                planner = PlannerAgent(self)
                plan = planner.plan(task, profile, context_pack, event_callback, turn)
                evidence.write_trace("model_plan", {"turn": turn, **plan.model_dump()})
                emit_event(
                    event_callback,
                    "model_plan",
                    f"Model Plan [turn {turn}]: {plan.summary}",
                    turn=turn,
                    step_count=len(plan.steps),
                    status="planned",
                )
                self.tool_registry.validate_plan(plan)
            except ModelClientError as exc:
                risks.append(exc.message)
                evidence.write_trace("model_error", {"turn": turn, **exc.to_trace_payload()})
                emit_event(event_callback, "model_error", exc.message, turn=turn, code=exc.code)
                return "failed", turns_completed, exc.message
            turns_completed = turn
            plan_summaries.append(f"Plan [turn {turn}]: {plan.summary}")
            if not plan.steps:
                return status, turns_completed, recent_error
            for step in plan.steps:
                if cancel_requested(cancel_check):
                    message = f"Run cancelled by user before execution of tool: {step.tool}"
                    risks.append(message)
                    evidence.write_trace("cancel_requested", {"stage": "before_tool", "turn": turn, "tool": step.tool})
                    emit_event(event_callback, "run_cancelled", message, run_id=evidence.run_id, turn=turn)
                    return "cancelled", turns_completed, message
                emit_event(
                    event_callback, "tool_start", f"Tool execution started: {step.tool}", turn=turn, tool=step.tool
                )
                try:
                    result, trace, policy = kernel.execute_tool(tool_context, step, turn, event_callback)
                    if result is None or trace is None:
                        recent_error = policy.reason
                        risks.append(recent_error)
                        return "failed", turns_completed, recent_error
                    emit_event(
                        event_callback,
                        "tool_result",
                        "Tool execution completed.",
                        turn=turn,
                        tool=step.tool,
                        status=result.status,
                        summary=result.summary,
                    )
                    tool_summaries.append(f"{step.tool}: {result.status}: {result.summary}")
                    if result.status != "success":
                        recent_error = result.error or result.summary or f"{step.tool} failed"
                        risks.append(recent_error)
                        return "failed", turns_completed, recent_error
                    changed_files.extend(result.changed_files)
                    if result.evidence_kind and result.evidence_source and result.evidence_summary:
                        entry = evidence.write_evidence(
                            result.evidence_kind,
                            result.evidence_source,
                            result.evidence_summary,
                            f"trace://{trace.id}",
                            confidence=0.9 if result.evidence_kind == "patch" else 0.8,
                        )
                        evidence_entries.append(entry)
                        if result.evidence_kind == "patch":
                            evidence.write_trace(
                                "patch_evidence_binding",
                                {
                                    "turn": turn,
                                    "tool_trace_id": trace.id,
                                    "evidence_id": entry.id,
                                    "changed_files": result.changed_files,
                                },
                            )
                except (OSError, ValueError, RuntimeError) as exc:
                    recent_error = f"Tool execution error: {exc}"
                    risks.append(recent_error)
                    evidence.write_trace(
                        "tool_error", {"turn": turn, "tool": step.tool, "error": str(exc), "fatal": False}
                    )
                    return "failed", turns_completed, recent_error
                except Exception as exc:  # noqa: BLE001
                    recent_error = f"Fatal unexpected error: {exc}"
                    risks.append(recent_error)
                    evidence.write_trace(
                        "tool_error", {"turn": turn, "tool": step.tool, "error": str(exc), "fatal": True}
                    )
                    return "failed", turns_completed, recent_error
            if _should_stop_after_turn(profile, changed_files, plan.steps):
                return status, turns_completed, recent_error
        message = f"Model did not finish within {turn_limit} turn(s)."
        risks.append(message)
        evidence.write_trace("model_error", {"code": "max_turns_exceeded", "message": message})
        return "failed", turns_completed, message


def _max_model_turns(profile: ModelProfile) -> int:
    return 2 if profile.provider == "mock" else 4


def _should_stop_after_turn(profile: ModelProfile, changed_files: list[str], steps: Sequence[object]) -> bool:
    if profile.provider == "mock":
        return True
    if changed_files:
        return True
    return not steps
