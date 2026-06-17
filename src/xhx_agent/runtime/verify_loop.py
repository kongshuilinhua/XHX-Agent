from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from xhx_agent.evidence.report import write_report
from xhx_agent.evidence.store import EvidenceEntry, EvidenceStore
from xhx_agent.repo_intel.scanner import ProjectScan, scan_project
from xhx_agent.runtime.config import load_config
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.runtime.profiles import ModelProfile, get_profile
from xhx_agent.safety.checkpoint import Checkpoint, checkpoint_path, restore_plan_path
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.safety.repair import MAX_REPAIR_ATTEMPTS, RepairDecision, decide_repair
from xhx_agent.tools.registry import ToolContext
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.verification.router import infer_verification

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RuntimeApp

from xhx_agent.runtime.utils import cancel_requested, new_run_id
from xhx_agent.safety.policy import PolicyDecision

ConfirmationCallback = Callable[[str, PolicyDecision], bool]
CancelCheck = Callable[[], bool]


@dataclass
class VerificationLoopContext:
    task: str
    run_id: str
    profile: ModelProfile
    scan: ProjectScan
    evidence: EvidenceStore
    kernel: SafeExecutionKernel
    tool_context: ToolContext
    metrics_tracker: dict[str, int]
    assume_yes: bool = False
    confirm_callback: ConfirmationCallback | None = None
    auto_repair: bool = False
    cancel_check: CancelCheck | None = None
    event_callback: EventCallback | None = None

    # Mutable state fields tracking execution progress:
    status: str = "success"
    verification_status: str = "not_executed"
    changed_files: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    verification_results: list[TerminalResult] = field(default_factory=list)
    repair_attempts: int = 0
    turns_completed: int = 0
    recent_error: str | None = None
    tool_summaries: list[str] = field(default_factory=list)
    evidence_entries: list[EvidenceEntry] = field(default_factory=list)
    plan_summaries: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


class ManualVerificationResult(BaseModel):
    run_id: str
    status: str
    changed_files: list[str]
    commands: list[str]
    verification_results: list[TerminalResult] = []
    summary_path: str | None = None
    risk_summary: list[str]


class ManualRepairResult(BaseModel):
    run_id: str
    status: str
    turns: int = 0
    changed_files: list[str]
    commands: list[str]
    verification: str
    verification_results: list[TerminalResult] = []
    repair_attempts: int = 0
    summary_path: str | None = None
    restore_plan_path: str | None = None
    risk_summary: list[str]


def _manual_repair_attempt_limit(max_attempts: int) -> int:
    return max(1, min(max_attempts, MAX_REPAIR_ATTEMPTS))


def _last_verification_error(results: list[TerminalResult]) -> str:
    if not results:
        return "Verification failed."
    last = results[-1]
    exit_code = "none" if last.exit_code is None else str(last.exit_code)
    return f"{last.command} failed with exit_code={exit_code}: {last.summary or last.policy.reason}"


def checkpoint_path_value(workspace: Path, run_id: str) -> Path:
    return checkpoint_path(workspace, run_id).relative_to(workspace)


def restore_plan_path_value(workspace: Path, run_id: str) -> Path:
    return restore_plan_path(workspace, run_id).relative_to(workspace)


def _refresh_repo_intel_index(
    workspace: Path,
    evidence: EvidenceStore,
    event_callback: EventCallback | None,
    risks: list[str],
) -> None:
    from xhx_agent.repo_intel.index import write_repo_intel_index

    try:
        path = write_repo_intel_index(workspace)
    except Exception as exc:  # noqa: BLE001 - repo index refresh should not discard a successful patch
        message = f"Repo intelligence index refresh failed: {exc}"
        risks.append(message)
        evidence.write_trace("repo_index_refresh", {"status": "failed", "error": str(exc)})
        emit_event(
            event_callback,
            "repo_index_refresh",
            "Repo intelligence index refresh failed.",
            status="failed",
            error=str(exc),
        )
        return
    relative_path = path.relative_to(workspace).as_posix()
    evidence.write_trace("repo_index_refresh", {"status": "success", "path": relative_path})
    emit_event(
        event_callback, "repo_index_refresh", "Repo intelligence index refreshed.", status="success", path=relative_path
    )


class VerificationLoop:
    def __init__(self, app: RuntimeApp) -> None:
        self.app = app
        self.workspace = app.workspace
        self.tool_registry = app.tool_registry

    def execute_verification_loop(
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
        run_status = "success"
        verification_status = "passed" if commands else "not_executed"

        for command in commands:
            if cancel_requested(cancel_check):
                run_status = "cancelled"
                verification_status = "cancelled"
                stage = "manual_verification" if manual else "before_verification_command"
                risks.append(f"Verification cancelled by user before command: {command}")
                evidence.write_trace("cancel_requested", {"stage": stage, "command": command})
                emit_event(
                    event_callback,
                    "run_cancelled",
                    "Verification cancelled before command execution.",
                    run_id=run_id,
                    command=command,
                )
                break

            start_event_name = "Manual verification started." if manual else "Verification started."
            emit_event(event_callback, "verification_start", start_event_name, command=command)

            result = kernel.run_verification(
                command,
                assume_yes=assume_yes,
                confirm_callback=confirm_callback,
                event_callback=event_callback,
            )

            finish_event_name = "Manual verification finished." if manual else "Verification finished."
            emit_event(
                event_callback,
                "verification_result",
                finish_event_name,
                command=command,
                status=result.status,
                exit_code=result.exit_code,
            )

            commands_run_accumulated.append(command)
            results_accumulated.append(result)

            if result.status == "confirm":
                verification_status = "requires_confirmation"
                risks.append(f"Verification requires confirmation: {command}. {result.summary or result.policy.reason}")
                break
            if result.status != "success":
                run_status = "failed"
                verification_status = "failed"
                risks.append(f"Verification failed: {command}. exit_code={result.exit_code}")
                break
            verification_status = "passed"

        return run_status, verification_status

    def verify_changed_files(
        self,
        changed_files: list[str],
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> ManualVerificationResult:
        run_id = new_run_id("verify")
        normalized_changed_files = sorted(set(changed_files))
        evidence = EvidenceStore(self.workspace, run_id)
        kernel = SafeExecutionKernel(self.workspace, run_id, evidence, self.tool_registry)
        evidence.write_trace("run_start", {"task": "manual verification", "changed_files": normalized_changed_files})
        emit_event(
            event_callback,
            "run_start",
            "Manual verification started.",
            run_id=run_id,
            task="manual verification",
            profile="manual",
        )
        if not normalized_changed_files:
            status = "skipped_no_changes"
            evidence.write_trace("verification_skipped", {"reason": "No changed files."})
            summary = write_report(
                workspace=self.workspace,
                run_id=run_id,
                task="manual verification",
                plan=["Infer verification commands from changed files."],
                changed_files=[],
                commands=[],
                verification=status,
                risks=[],
            )
            evidence.write_trace("run_end", {"status": "success", "summary_path": str(summary)})
            emit_event(
                event_callback,
                "run_end",
                "Manual verification finished.",
                run_id=run_id,
                status="success",
                verification=status,
                changed_files=[],
                summary_path=str(summary.relative_to(self.workspace)),
            )
            return ManualVerificationResult(
                run_id=run_id,
                status=status,
                changed_files=[],
                commands=[],
                summary_path=str(summary.relative_to(self.workspace)),
                risk_summary=[],
            )

        plan = infer_verification(self.workspace, normalized_changed_files)
        commands = [item.command for item in plan.commands]
        risks: list[str] = []
        results: list[TerminalResult] = []
        verification_status = plan.skip_reason or "not_executed"
        if not commands:
            risks.append(plan.skip_reason or "No verification command inferred.")
        run_status, verification_status = self.execute_verification_loop(
            kernel=kernel,
            commands=commands,
            assume_yes=assume_yes,
            confirm_callback=confirm_callback,
            event_callback=event_callback,
            cancel_check=cancel_check,
            run_id=run_id,
            evidence=evidence,
            risks=risks,
            commands_run_accumulated=[],
            results_accumulated=results,
            manual=True,
        )
        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task="manual verification",
            plan=["Infer verification commands from changed files.", "Run selected verification commands."],
            changed_files=normalized_changed_files,
            commands=commands,
            verification=verification_status,
            risks=risks,
            verification_results=results,
        )
        evidence.write_trace("run_end", {"status": verification_status, "summary_path": str(summary)})
        emit_event(
            event_callback,
            "run_end",
            "Manual verification finished.",
            run_id=run_id,
            status="success" if verification_status in {"passed", "skipped_no_changes"} else verification_status,
            verification=verification_status,
            changed_files=normalized_changed_files,
            summary_path=str(summary.relative_to(self.workspace)),
        )
        return ManualVerificationResult(
            run_id=run_id,
            status=verification_status,
            changed_files=normalized_changed_files,
            commands=commands,
            verification_results=results,
            summary_path=str(summary.relative_to(self.workspace)),
            risk_summary=risks,
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
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        attempt_limit = _manual_repair_attempt_limit(max_attempts)
        run_id = new_run_id("repair")
        normalized_changed_files = sorted(set(changed_files))
        evidence = EvidenceStore(self.workspace, run_id)
        kernel = SafeExecutionKernel(self.workspace, run_id, evidence, self.tool_registry)
        evidence.write_trace(
            "run_start",
            {
                "task": f"manual repair: {task}",
                "changed_files": normalized_changed_files,
                "verification_results": [res.model_dump() for res in failed_verification_results],
            },
        )
        emit_event(
            event_callback,
            "run_start",
            "Manual repair started.",
            run_id=run_id,
            task=f"manual repair: {task}",
            profile=profile.name,
        )
        risks: list[str] = []
        commands_run: list[str] = []
        verification_results: list[TerminalResult] = []
        checkpoint: Checkpoint | None = None
        restore_plan_created = False
        repair_decision: RepairDecision | None = None
        repair_attempts = 0
        turns_completed = 0
        status = "success"

        failed_results = [result for result in failed_verification_results if result.status == "failed"]
        if not failed_results:
            verification_status = "skipped_no_failed_verification"
            risks.append("Manual repair requires a failed verification result.")
            summary = write_report(
                workspace=self.workspace,
                run_id=run_id,
                task=f"manual repair: {task}",
                plan=["Check failed verification state."],
                changed_files=normalized_changed_files,
                commands=[],
                verification=verification_status,
                risks=risks,
                verification_results=failed_verification_results,
            )
            evidence.write_trace("run_end", {"status": verification_status, "summary_path": str(summary)})
            emit_event(
                event_callback,
                "run_end",
                "Manual repair skipped.",
                run_id=run_id,
                status=verification_status,
                verification=verification_status,
                changed_files=normalized_changed_files,
                summary_path=str(summary.relative_to(self.workspace)),
            )
            return ManualRepairResult(
                run_id=run_id,
                status=verification_status,
                turns=turns_completed,
                changed_files=normalized_changed_files,
                commands=[],
                verification=verification_status,
                verification_results=failed_verification_results,
                summary_path=str(summary.relative_to(self.workspace)),
                risk_summary=risks,
            )

        scan = scan_project(self.workspace)
        emit_event(
            event_callback,
            "scan",
            "Project scan completed.",
            detected_languages=scan.detected_languages,
            file_count=scan.file_count,
        )
        current_failed_results = list(failed_results)
        plan_summaries = [
            "Manual repair requested after failed verification.",
            f"Scan project languages: {', '.join(scan.detected_languages) or 'unknown'}.",
        ]
        evidence_entries: list[EvidenceEntry] = []
        recent_error: str | None = _last_verification_error(current_failed_results)
        while repair_attempts < attempt_limit:
            repair_decision = decide_repair("failed", attempts_used=repair_attempts, auto_repair_enabled=True)
            evidence.write_trace("repair_decision", repair_decision.model_dump())
            emit_event(
                event_callback,
                "repair_decision",
                repair_decision.reason,
                should_repair=repair_decision.should_repair,
                attempts_used=repair_decision.attempts_used,
                max_attempts=attempt_limit,
            )
            if not repair_decision.should_repair:
                risks.append(f"Manual repair not attempted: {repair_decision.reason}")
                status = "failed"
                break
            if cancel_requested(cancel_check):
                verification_status = "cancelled"
                status = "cancelled"
                risks.append("Manual repair cancelled by user before repair attempt.")
                evidence.write_trace("cancel_requested", {"stage": "before_manual_repair_attempt"})
                emit_event(
                    event_callback, "run_cancelled", "Manual repair cancelled before repair attempt.", run_id=run_id
                )
                break

            repair_attempts += 1
            emit_event(
                event_callback,
                "repair_start",
                "Manual repair attempt started.",
                attempt=repair_attempts,
                max_attempts=attempt_limit,
            )
            evidence.write_evidence(
                "decision",
                "manual-repair",
                f"Manual repair attempt {repair_attempts}/{attempt_limit}: {repair_decision.reason}",
                f"trace://{run_id}/repair/{repair_attempts}",
                confidence=0.75,
            )
            mutable_changed_files = list(normalized_changed_files)
            tool_summaries = [f"verification failed: {_last_verification_error(current_failed_results)}"]
            status, turns_completed, recent_error = self.app._run_model_tool_loop(
                task=f"Manual repair attempt {repair_attempts}/{attempt_limit} after failed verification: {task}",
                profile=profile,
                scan=scan,
                evidence=evidence,
                kernel=kernel,
                tool_context=ToolContext(workspace=self.workspace, max_file_bytes=config.max_file_bytes),
                changed_files=mutable_changed_files,
                tool_summaries=tool_summaries,
                evidence_entries=evidence_entries,
                plan_summaries=plan_summaries,
                risks=risks,
                recent_error=recent_error,
                starting_turn=1,
                max_turns=1,
                event_callback=event_callback,
                cancel_check=cancel_check,
                metrics_tracker={"tokens": 0},
                assume_yes=assume_yes,
            )
            normalized_changed_files = sorted(set(mutable_changed_files))
            if status not in {"failed", "cancelled"}:
                _refresh_repo_intel_index(self.workspace, evidence, event_callback, risks)
                checkpoint = kernel.create_checkpoint(normalized_changed_files)
                emit_event(
                    event_callback,
                    "checkpoint",
                    "Checkpoint created.",
                    checkpoint_id=checkpoint.id,
                    changed_files=normalized_changed_files,
                )
                plan = infer_verification(self.workspace, normalized_changed_files)
                commands_run = [item.command for item in plan.commands]
                verification_status = plan.skip_reason or "not_executed"
                if not commands_run:
                    risks.append(plan.skip_reason or "No verification command inferred.")
                status, verification_status = self.execute_verification_loop(
                    kernel=kernel,
                    commands=commands_run,
                    assume_yes=assume_yes,
                    confirm_callback=confirm_callback,
                    event_callback=event_callback,
                    cancel_check=cancel_check,
                    run_id=run_id,
                    evidence=evidence,
                    risks=risks,
                    commands_run_accumulated=[],
                    results_accumulated=verification_results,
                    manual=True,
                )
                if status == "failed" and verification_results:
                    current_failed_results = [verification_results[-1]]
                if verification_status == "passed":
                    status = "success"
                    break
                if verification_status in {"requires_confirmation", "cancelled"}:
                    break
            else:
                verification_status = "not_executed"
                if recent_error:
                    risks.append(recent_error)
                break
            if verification_status != "failed":
                break
        if status == "failed" and verification_status == "failed" and repair_attempts >= attempt_limit:
            repair_decision = RepairDecision(
                should_repair=False,
                attempts_used=repair_attempts,
                max_attempts=attempt_limit,
                reason="Manual repair attempt limit reached.",
            )
            evidence.write_trace("repair_decision", repair_decision.model_dump())
            emit_event(
                event_callback,
                "repair_decision",
                repair_decision.reason,
                should_repair=False,
                attempts_used=repair_attempts,
                max_attempts=attempt_limit,
            )
            risks.append(repair_decision.reason)
        if status == "failed" and checkpoint is not None:
            kernel.create_restore_plan(checkpoint)
            restore_plan_created = True
            emit_event(event_callback, "restore_plan", "Restore plan created.", run_id=run_id)
        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=f"manual repair: {task}",
            plan=[
                "Check failed verification state.",
                f"Run up to {attempt_limit} manual repair attempt(s).",
                "Verify repaired changed files.",
            ],
            changed_files=normalized_changed_files,
            commands=commands_run,
            verification=verification_status,
            risks=risks,
            verification_results=verification_results or failed_verification_results,
            checkpoint_path=str(checkpoint_path_value(self.workspace, run_id)) if checkpoint else None,
            restore_plan_path=str(restore_plan_path_value(self.workspace, run_id)) if restore_plan_created else None,
            repair=RepairDecision(
                should_repair=False,
                attempts_used=repair_attempts,
                max_attempts=attempt_limit,
                reason=f"Manual repair performs at most {attempt_limit} attempt(s) in v0.5.",
            ),
            repair_attempts=repair_attempts,
        )
        evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        emit_event(
            event_callback,
            "run_end",
            "Manual repair finished.",
            run_id=run_id,
            status=status,
            verification=verification_status,
            changed_files=normalized_changed_files,
            summary_path=str(summary.relative_to(self.workspace)),
        )
        return ManualRepairResult(
            run_id=run_id,
            status=status,
            turns=turns_completed,
            changed_files=normalized_changed_files,
            commands=commands_run,
            verification=verification_status,
            verification_results=verification_results,
            repair_attempts=repair_attempts,
            summary_path=str(summary.relative_to(self.workspace)),
            restore_plan_path=str(restore_plan_path_value(self.workspace, run_id)) if restore_plan_created else None,
            risk_summary=risks,
        )
