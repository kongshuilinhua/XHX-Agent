from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from pydantic import BaseModel

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.context.debug import write_context_debug_report
from xhx_agent.context.pack import ContextPack
from xhx_agent.evidence.store import EvidenceEntry
from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.evidence.report import write_report
from xhx_agent.models.mock import MockModelClient
from xhx_agent.models.openai_compatible import OpenAICompatibleClient
from xhx_agent.models.types import ModelClientError, ModelPlan
from xhx_agent.repo_intel.scanner import scan_project
from xhx_agent.repo_intel.xhx_md import write_xhx_md
from xhx_agent.runtime.config import load_config, write_default_config
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.runtime.paths import ensure_xhx_dirs
from xhx_agent.runtime.profiles import ModelProfile, get_profile, write_default_profiles
from xhx_agent.safety.checkpoint import Checkpoint, checkpoint_path, restore_plan_path
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.safety.repair import MAX_REPAIR_ATTEMPTS, RepairDecision, decide_repair
from xhx_agent.tools.registry import ToolContext, ToolRegistry, default_tool_registry
from xhx_agent.tools.terminal import TerminalResult
from xhx_agent.verification.router import infer_verification


class InitResult(BaseModel):
    config_created: bool
    profiles_created: bool
    xhx_md_created: bool


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


class PlanPreview(BaseModel):
    run_id: str
    status: str
    summary: str
    step_count: int
    context_budget_tokens: int
    context_used_tokens_estimate: int
    trace_path: str
    risk_summary: list[str]


ConfirmationCallback = Callable[[str, object], bool]


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
        return InitResult(
            config_created=config_created,
            profiles_created=profiles_created,
            xhx_md_created=xhx_md_created,
        )

    def run_task(
        self,
        task: str,
        profile_name: str | None = None,
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        auto_repair: bool = False,
        event_callback: EventCallback | None = None,
    ) -> RunResult:
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        run_id = f"run-{int(time.time())}"
        evidence = EvidenceStore(self.workspace, run_id)
        kernel = SafeExecutionKernel(self.workspace, run_id, evidence, self.tool_registry)
        emit_event(event_callback, "run_start", "Run started.", run_id=run_id, task=task, profile=profile.name)
        evidence.write_trace("run_start", {"task": task, "profile": profile.name})
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
        )

        verification_plan = infer_verification(self.workspace, changed_files) if changed_files else None
        commands = [item.command for item in verification_plan.commands] if verification_plan else []
        verification_status = (
            "not_executed" if status == "failed" else verification_plan.skip_reason if verification_plan else "skipped_no_changes"
        )
        if status != "failed" and not changed_files:
            verification_status = "skipped_no_changes"
            evidence.write_trace("verification_skipped", {"reason": "No changed files."})

        while status != "failed" and changed_files:
            checkpoint = kernel.create_checkpoint(sorted(set(changed_files)))
            emit_event(
                event_callback,
                "checkpoint",
                "Checkpoint created.",
                checkpoint_id=checkpoint.id,
                changed_files=sorted(set(changed_files)),
            )
            verification_results.clear()
            commands_run.clear()
            verification_status = verification_plan.skip_reason if verification_plan else "not_executed"
            for command in commands:
                emit_event(event_callback, "verification_start", "Verification started.", command=command)
                result = kernel.run_verification(
                    command,
                    assume_yes=assume_yes,
                    confirm_callback=confirm_callback,
                    event_callback=event_callback,
                )
                emit_event(
                    event_callback,
                    "verification_result",
                    "Verification finished.",
                    command=command,
                    status=result.status,
                    exit_code=result.exit_code,
                )
                commands_run.append(command)
                verification_results.append(result)
                if result.status == "confirm":
                    verification_status = "requires_confirmation"
                    risks.append(f"Verification requires confirmation: {command}. {result.summary or result.policy.reason}")
                    break
                if result.status != "success":
                    status = "failed"
                    verification_status = "failed"
                    risks.append(f"Verification failed: {command}. exit_code={result.exit_code}")
                    break
                verification_status = "passed"
            repair_decision = decide_repair(verification_status, attempts_used=repair_attempts, auto_repair_enabled=auto_repair)
            evidence.write_trace("repair_decision", repair_decision.model_dump())
            emit_event(
                event_callback,
                "repair_decision",
                repair_decision.reason,
                should_repair=repair_decision.should_repair,
                attempts_used=repair_decision.attempts_used,
                max_attempts=repair_decision.max_attempts,
            )
            if verification_status != "failed":
                break
            if not repair_decision.should_repair:
                evidence.write_evidence(
                    "error",
                    "repair",
                    repair_decision.reason,
                    f"trace://{run_id}/repair_decision",
                    confidence=0.8,
                )
                risks.append(f"Repair not attempted: {repair_decision.reason}")
                break
            repair_attempts += 1
            emit_event(
                event_callback,
                "repair_start",
                "Repair attempt started.",
                attempt=repair_attempts,
                max_attempts=MAX_REPAIR_ATTEMPTS,
            )
            evidence.write_evidence(
                "decision",
                "repair",
                f"Repair attempt {repair_attempts}/{MAX_REPAIR_ATTEMPTS}: {repair_decision.reason}",
                f"trace://{run_id}/repair/{repair_attempts}",
                confidence=0.7,
            )
            recent_error = _last_verification_error(verification_results)
            tool_summaries.append(f"verification failed: {recent_error}")
            before_repair_changed = len(changed_files)
            status = "success"
            status, turns_completed, recent_error = self._run_model_tool_loop(
                task=f"Repair after failed verification: {task}",
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
                starting_turn=turns_completed + 1,
                max_turns=1,
                event_callback=event_callback,
            )
            if status == "failed":
                break
            if len(changed_files) == before_repair_changed:
                status = "failed"
                message = "Repair loop produced no additional changes."
                risks.append(message)
                evidence.write_trace("repair_decision", {"should_repair": False, "reason": message, "attempts_used": repair_attempts})
                break
            continue
        if status == "failed" and checkpoint is not None:
            kernel.create_restore_plan(checkpoint)
            restore_plan_created = True
            emit_event(event_callback, "restore_plan", "Restore plan created.", run_id=run_id)
        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=task,
            plan=plan_summaries + ["Write run summary."],
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            risks=risks,
            verification_results=verification_results,
            checkpoint_path=str(checkpoint_path_value(self.workspace, run_id)) if checkpoint else None,
            restore_plan_path=str(restore_plan_path_value(self.workspace, run_id)) if restore_plan_created else None,
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
            summary_path=str(summary.relative_to(self.workspace)),
        )
        return RunResult(
            run_id=run_id,
            status=status,
            turns=turns_completed,
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            verification_results=verification_results,
            checkpoint_path=str(checkpoint_path_value(self.workspace, run_id)) if checkpoint else None,
            restore_plan_path=str(restore_plan_path_value(self.workspace, run_id)) if restore_plan_created else None,
            repair=repair_decision,
            repair_attempts=repair_attempts,
            summary_path=str(summary.relative_to(self.workspace)),
            risk_summary=risks,
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
        run_id = f"dry-run-{int(time.time())}"
        evidence = EvidenceStore(self.workspace, run_id)
        evidence.write_trace("run_start", {"task": task, "profile": profile.name, "dry_run": True})
        scan = scan_project(self.workspace)
        context_pack = compile_context_pack(workspace=self.workspace, task=task, scan=scan)
        context_debug = write_context_debug_report(self.workspace, run_id, 1, context_pack)
        evidence.write_trace("context_pack", context_pack.model_dump())
        evidence.write_trace("context_debug_report", {"turn": 1, "path": str(context_debug.relative_to(self.workspace))})
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

    def _build_plan(self, task: str, profile: ModelProfile, context_pack: ContextPack) -> ModelPlan:
        if profile.provider == "mock":
            return MockModelClient().plan(task, self.workspace)
        if profile.provider == "openai-compatible":
            return OpenAICompatibleClient(
                base_url=profile.base_url,
                api_key_env=profile.api_key_env,
                model=profile.model,
                temperature=profile.temperature,
            ).plan(task, context_pack)
        raise ModelClientError(
            code="unsupported_provider",
            message=f"Unsupported model provider: {profile.provider}",
            details={"provider": profile.provider},
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
    ) -> tuple[str, int, str | None]:
        status = "success"
        turns_completed = starting_turn - 1
        turn_limit = max_turns or _max_model_turns(profile)
        for offset in range(turn_limit):
            turn = starting_turn + offset
            context_pack = compile_context_pack(
                workspace=self.workspace,
                task=task,
                scan=scan,
                changed_files=changed_files,
                tool_summaries=tool_summaries,
                evidence_entries=evidence_entries,
                recent_error=recent_error,
            )
            context_debug = write_context_debug_report(self.workspace, evidence.run_id, turn, context_pack)
            evidence.write_trace("context_pack", context_pack.model_dump())
            evidence.write_trace("context_debug_report", {"turn": turn, "path": str(context_debug.relative_to(self.workspace))})
            emit_event(
                event_callback,
                "context_pack",
                "Context pack compiled.",
                turn=turn,
                selected=len(context_pack.items),
                omitted=len(context_pack.omitted),
                used_tokens_estimate=context_pack.used_tokens_estimate,
                budget_tokens=context_pack.budget_tokens,
            )
            try:
                emit_event(event_callback, "model_plan_start", "Building model plan.", turn=turn, profile=profile.name)
                plan = self._build_plan(task, profile, context_pack)
            except ModelClientError as exc:
                evidence.write_trace("model_error", exc.to_trace_payload())
                risks.append(exc.message)
                return "failed", turns_completed, exc.message

            plan_trace_type = "mock_plan" if profile.provider == "mock" else "model_plan"
            evidence.write_trace(plan_trace_type, {"turn": turn, **plan.model_dump()})
            emit_event(
                event_callback,
                "model_plan",
                plan.summary,
                turn=turn,
                step_count=len(plan.steps),
                status=plan.status,
            )
            turns_completed = turn
            plan_summaries.append(f"Turn {turn}: {plan.summary}")
            evidence_entries.append(
                evidence.write_evidence(
                    kind="decision",
                    source=profile.provider,
                    summary=plan.summary,
                    artifact_ref=f"trace://{evidence.run_id}/{plan_trace_type}/{turn}",
                    confidence=0.7 if profile.provider == "mock" else 0.6,
                )
            )

            if plan.status == "done":
                return status, turns_completed, recent_error

            try:
                self.tool_registry.validate_plan(plan)
            except ModelClientError as exc:
                risks.append(exc.message)
                evidence.write_trace("model_error", exc.to_trace_payload())
                return "failed", turns_completed, exc.message

            for step in plan.steps:
                try:
                    emit_event(event_callback, "tool_start", f"Tool started: {step.tool}", turn=turn, tool=step.tool)
                    result, trace, policy = kernel.execute_tool(tool_context, step, turn, event_callback)
                    if result is None or trace is None:
                        risks.append(policy.reason)
                        return "failed", turns_completed, policy.reason
                    emit_event(
                        event_callback,
                        "tool_result",
                        f"Tool finished: {step.tool}",
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
                except Exception as exc:  # noqa: BLE001 - convert tool failures into run result
                    recent_error = str(exc)
                    risks.append(recent_error)
                    evidence.write_trace("tool_error", {"turn": turn, "tool": step.tool, "error": str(exc)})
                    return "failed", turns_completed, recent_error
            if _should_stop_after_turn(profile, changed_files, plan.steps):
                return status, turns_completed, recent_error
        message = f"Model did not finish within {turn_limit} turn(s)."
        risks.append(message)
        evidence.write_trace("model_error", {"code": "max_turns_exceeded", "message": message})
        return "failed", turns_completed, message


def _max_model_turns(profile: ModelProfile) -> int:
    return 2 if profile.provider == "mock" else 4


def _should_stop_after_turn(profile: ModelProfile, changed_files: list[str], steps: list[object]) -> bool:
    if profile.provider == "mock":
        return True
    if changed_files:
        return True
    return not steps


def checkpoint_path_value(workspace: Path, run_id: str) -> Path:
    return checkpoint_path(workspace, run_id).relative_to(workspace)


def restore_plan_path_value(workspace: Path, run_id: str) -> Path:
    return restore_plan_path(workspace, run_id).relative_to(workspace)


def _last_verification_error(results: list[TerminalResult]) -> str:
    if not results:
        return "Verification failed."
    last = results[-1]
    exit_code = "none" if last.exit_code is None else str(last.exit_code)
    return f"{last.command} failed with exit_code={exit_code}: {last.summary or last.policy.reason}"
