from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel

from xhx_agent.context.compiler import compile_context_pack
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
from xhx_agent.runtime.paths import ensure_xhx_dirs
from xhx_agent.runtime.profiles import ModelProfile, get_profile, write_default_profiles
from xhx_agent.tools.registry import ToolContext, ToolRegistry, default_tool_registry
from xhx_agent.tools.terminal import run_terminal
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

    def run_task(self, task: str, profile_name: str | None = None, assume_yes: bool = False) -> RunResult:
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        run_id = f"run-{int(time.time())}"
        evidence = EvidenceStore(self.workspace, run_id)
        evidence.write_trace("run_start", {"task": task, "profile": profile.name})
        scan = scan_project(self.workspace)
        changed_files: list[str] = []
        commands_run: list[str] = []
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

        for turn in range(1, _max_model_turns(profile) + 1):
            context_pack = compile_context_pack(
                workspace=self.workspace,
                task=task,
                scan=scan,
                changed_files=changed_files,
                tool_summaries=tool_summaries,
                evidence_entries=evidence_entries,
                recent_error=recent_error,
            )
            evidence.write_trace("context_pack", context_pack.model_dump())
            try:
                plan = self._build_plan(task, profile, context_pack)
            except ModelClientError as exc:
                evidence.write_trace("model_error", exc.to_trace_payload())
                status = "failed"
                risks.append(exc.message)
                break

            plan_trace_type = "mock_plan" if profile.provider == "mock" else "model_plan"
            evidence.write_trace(plan_trace_type, {"turn": turn, **plan.model_dump()})
            turns_completed = turn
            plan_summaries.append(f"Turn {turn}: {plan.summary}")
            evidence_entries.append(
                evidence.write_evidence(
                    kind="decision",
                    source=profile.provider,
                    summary=plan.summary,
                    artifact_ref=f"trace://{run_id}/{plan_trace_type}/{turn}",
                    confidence=0.7 if profile.provider == "mock" else 0.6,
                )
            )

            if plan.status == "done":
                break

            try:
                self.tool_registry.validate_plan(plan)
            except ModelClientError as exc:
                status = "failed"
                risks.append(exc.message)
                evidence.write_trace("model_error", exc.to_trace_payload())
                break

            for step in plan.steps:
                trace = evidence.write_trace("tool_call", {"turn": turn, **step.model_dump()})
                try:
                    result = self.tool_registry.execute(tool_context, step)
                    evidence.write_trace("tool_result", {"turn": turn, **result.trace_payload})
                    tool_summaries.append(f"{step.tool}: {result.status}: {result.summary}")
                    if result.status != "success":
                        status = "failed"
                        recent_error = result.error or result.summary or f"{step.tool} failed"
                        risks.append(recent_error)
                        break
                    changed_files.extend(result.changed_files)
                    if result.evidence_kind and result.evidence_source and result.evidence_summary:
                        evidence_entries.append(
                            evidence.write_evidence(
                                result.evidence_kind,
                                result.evidence_source,
                                result.evidence_summary,
                                f"trace://{trace.id}",
                                confidence=0.9 if result.evidence_kind == "patch" else 0.8,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - convert tool failures into run result
                    status = "failed"
                    recent_error = str(exc)
                    risks.append(recent_error)
                    evidence.write_trace("tool_error", {"turn": turn, "tool": step.tool, "error": str(exc)})
                    break
            if status == "failed" or plan.status == "done" or _should_stop_after_turn(profile, changed_files, plan.steps):
                break
        else:
            status = "failed"
            message = f"Model did not finish within {_max_model_turns(profile)} turn(s)."
            recent_error = message
            risks.append(message)
            evidence.write_trace("model_error", {"code": "max_turns_exceeded", "message": message})

        verification_plan = infer_verification(self.workspace, changed_files) if changed_files else None
        commands = [item.command for item in verification_plan.commands] if verification_plan else []
        verification_status = (
            "not_executed" if status == "failed" else verification_plan.skip_reason if verification_plan else "skipped_no_changes"
        )
        if status != "failed" and changed_files:
            for command in commands:
                result = run_terminal(self.workspace, command, assume_yes=assume_yes)
                commands_run.append(command)
                evidence.write_trace("verification", result.model_dump())
                evidence.write_evidence(
                    "test",
                    command,
                    f"{result.status}: {result.summary or result.policy.reason}",
                    f"trace://{run_id}/verification/{command}",
                    confidence=0.95 if result.status == "success" else 0.6,
                )
                if result.status == "confirm":
                    verification_status = "requires_confirmation"
                    risks.append(f"Verification requires confirmation: {command}")
                    break
                if result.status != "success":
                    status = "failed"
                    verification_status = "failed"
                    risks.append(f"Verification failed: {command}")
                    break
                verification_status = "passed"
        elif status != "failed":
            verification_status = "skipped_no_changes"
            evidence.write_trace("verification_skipped", {"reason": "No changed files."})
        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=task,
            plan=plan_summaries + ["Write run summary."],
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            risks=risks,
        )
        evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        return RunResult(
            run_id=run_id,
            status=status,
            turns=turns_completed,
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            summary_path=str(summary.relative_to(self.workspace)),
            risk_summary=risks,
        )

    def run_task_json(self, task: str, profile_name: str | None = None, assume_yes: bool = False) -> str:
        return json.dumps(self.run_task(task, profile_name, assume_yes=assume_yes).model_dump(), ensure_ascii=False, indent=2)

    def preview_plan(self, task: str, profile_name: str | None = None) -> PlanPreview:
        config = load_config(self.workspace)
        profile = get_profile(self.workspace, profile_name or config.default_profile)
        run_id = f"dry-run-{int(time.time())}"
        evidence = EvidenceStore(self.workspace, run_id)
        evidence.write_trace("run_start", {"task": task, "profile": profile.name, "dry_run": True})
        scan = scan_project(self.workspace)
        context_pack = compile_context_pack(workspace=self.workspace, task=task, scan=scan)
        evidence.write_trace("context_pack", context_pack.model_dump())
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


def _max_model_turns(profile: ModelProfile) -> int:
    return 2 if profile.provider == "mock" else 4


def _should_stop_after_turn(profile: ModelProfile, changed_files: list[str], steps: list[object]) -> bool:
    if profile.provider == "mock":
        return True
    if changed_files:
        return True
    return not steps
