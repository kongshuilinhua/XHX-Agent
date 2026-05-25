from __future__ import annotations

import json
import time
from pathlib import Path

from pydantic import BaseModel

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
    changed_files: list[str]
    commands: list[str]
    verification: str
    summary_path: str
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
        try:
            plan = self._build_plan(task, profile, scan.model_dump())
        except ModelClientError as exc:
            evidence.write_trace("model_error", exc.to_trace_payload())
            summary = write_report(
                workspace=self.workspace,
                run_id=run_id,
                task=task,
                plan=[
                    "Load project configuration.",
                    f"Scan project languages: {', '.join(scan.detected_languages) or 'unknown'}.",
                    "Stop before tool execution because model planning failed.",
                ],
                changed_files=[],
                commands=[],
                verification="not_executed",
                risks=[exc.message],
            )
            evidence.write_trace("run_end", {"status": "failed", "summary_path": str(summary)})
            return RunResult(
                run_id=run_id,
                status="failed",
                changed_files=[],
                commands=[],
                verification="not_executed",
                summary_path=str(summary.relative_to(self.workspace)),
                risk_summary=[exc.message],
            )

        plan_trace_type = "mock_plan" if profile.provider == "mock" else "model_plan"
        evidence.write_trace(plan_trace_type, plan.model_dump())
        evidence.write_evidence(
            kind="decision",
            source=profile.provider,
            summary=plan.summary,
            artifact_ref=f"trace://{run_id}/{plan_trace_type}",
            confidence=0.7 if profile.provider == "mock" else 0.6,
        )
        changed_files: list[str] = []
        commands_run: list[str] = []
        risks: list[str] = []
        status = "success"
        try:
            self.tool_registry.validate_plan(plan)
        except ModelClientError as exc:
            status = "failed"
            risks.append(exc.message)
            evidence.write_trace("model_error", exc.to_trace_payload())

        tool_context = ToolContext(workspace=self.workspace, max_file_bytes=config.max_file_bytes)
        for step in plan.steps:
            if status == "failed":
                break
            trace = evidence.write_trace("tool_call", step.model_dump())
            try:
                result = self.tool_registry.execute(tool_context, step)
                evidence.write_trace("tool_result", result.trace_payload)
                if result.status != "success":
                    status = "failed"
                    risks.append(result.error or result.summary or f"{step.tool} failed")
                    break
                changed_files.extend(result.changed_files)
                if result.evidence_kind and result.evidence_source and result.evidence_summary:
                    evidence.write_evidence(
                        result.evidence_kind,
                        result.evidence_source,
                        result.evidence_summary,
                        f"trace://{trace.id}",
                        confidence=0.9 if result.evidence_kind == "patch" else 0.8,
                    )
            except Exception as exc:  # noqa: BLE001 - convert tool failures into run result
                status = "failed"
                risks.append(str(exc))
                evidence.write_trace("tool_error", {"tool": step.tool, "error": str(exc)})
                break

        verification_plan = infer_verification(self.workspace, changed_files)
        commands = [item.command for item in verification_plan.commands]
        verification_status = verification_plan.skip_reason or "not_executed"
        if status != "failed":
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
        summary = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=task,
            plan=[
                "Load project configuration.",
                f"Scan project languages: {', '.join(scan.detected_languages) or 'unknown'}.",
                plan.summary,
                "Write run summary.",
            ],
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            risks=risks,
        )
        evidence.write_trace("run_end", {"status": status, "summary_path": str(summary)})
        return RunResult(
            run_id=run_id,
            status=status,
            changed_files=sorted(set(changed_files)),
            commands=commands_run or commands,
            verification=verification_status,
            summary_path=str(summary.relative_to(self.workspace)),
            risk_summary=risks,
        )

    def run_task_json(self, task: str, profile_name: str | None = None, assume_yes: bool = False) -> str:
        return json.dumps(self.run_task(task, profile_name, assume_yes=assume_yes).model_dump(), ensure_ascii=False, indent=2)

    def _build_plan(self, task: str, profile: ModelProfile, workspace_summary: dict[str, object]) -> ModelPlan:
        if profile.provider == "mock":
            return MockModelClient().plan(task, self.workspace)
        if profile.provider == "openai-compatible":
            return OpenAICompatibleClient(
                base_url=profile.base_url,
                api_key_env=profile.api_key_env,
                model=profile.model,
                temperature=profile.temperature,
            ).plan(task, workspace_summary)
        raise ModelClientError(
            code="unsupported_provider",
            message=f"Unsupported model provider: {profile.provider}",
            details={"provider": profile.provider},
        )
