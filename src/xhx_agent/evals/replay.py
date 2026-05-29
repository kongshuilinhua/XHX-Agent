from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from xhx_agent.evals.metrics import RunMetrics
from xhx_agent.evidence.report import write_report
from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.safety.repair import RepairDecision
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.terminal import TerminalResult

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class TrailReplayer:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def replay(self, run_id: str) -> RunResult:
        from xhx_agent.runtime.app import RunResult

        store = EvidenceStore(self.workspace, run_id)
        traces = store.list_traces()

        # Reconstructed fields
        task = ""
        status = "success"
        turns = 0
        changed_files: list[str] = []
        commands: list[str] = []
        verification = "not_executed"
        verification_results: list[TerminalResult] = []
        checkpoint_path: str | None = None
        restore_plan_path: str | None = None
        repair: RepairDecision | None = None
        repair_attempts = 0
        risk_summary: list[str] = []
        plan_summaries: list[str] = []
        duration_seconds = 0.0
        tokens_estimate = 0

        # Loop through traces to extract variables
        for entry in traces:
            payload = entry.payload
            if entry.type == "run_start":
                task = payload.get("task", "")
            elif entry.type == "context_pack":
                turns += 1
                tokens_estimate += payload.get("used_tokens_estimate", 0)
            elif entry.type in {"mock_plan", "model_plan"}:
                plan_summaries.append(payload.get("summary", ""))
            elif entry.type == "verification":
                # Parse policy decision if any
                policy_payload = payload.get("policy", {})
                policy_dec = PolicyDecision(
                    decision=policy_payload.get("decision", "allow"),
                    risk=RiskLevel(policy_payload.get("risk", "safe")),
                    reason=policy_payload.get("reason", ""),
                    requires_user=policy_payload.get("requires_user", False),
                )
                res = TerminalResult(
                    command=payload.get("command", ""),
                    status=payload.get("status", "success"),
                    exit_code=payload.get("exit_code"),
                    summary=payload.get("summary") or "",
                    policy=policy_dec,
                )
                verification_results.append(res)
            elif entry.type == "repair_decision":
                repair = RepairDecision(
                    should_repair=payload.get("should_repair", False),
                    attempts_used=payload.get("attempts_used", 0),
                    max_attempts=payload.get("max_attempts", 0),
                    reason=payload.get("reason", ""),
                )
            elif entry.type == "run_end":
                status = payload.get("status", status)
                changed_files = payload.get("changed_files", changed_files)
                commands = payload.get("commands", commands)
                verification = payload.get("verification", verification)
                checkpoint_path = payload.get("checkpoint_path")
                restore_plan_path = payload.get("restore_plan_path")
                repair_attempts = payload.get("repair_attempts", repair_attempts)
                risk_summary = payload.get("risk_summary", risk_summary)
                duration_seconds = payload.get("duration_seconds", 0.0)

        # Retrieve evidence entries
        evidence_entries = store.list_evidence()

        # Regenerate report in the logbook folder
        summary_path = write_report(
            workspace=self.workspace,
            run_id=run_id,
            task=task,
            plan=plan_summaries or ["Replay tasks summary."],
            changed_files=changed_files,
            commands=commands,
            verification=verification,
            risks=risk_summary,
            verification_results=verification_results,
            checkpoint_path=checkpoint_path,
            restore_plan_path=restore_plan_path,
            repair=repair,
            repair_attempts=repair_attempts,
            evidence_entries=evidence_entries,
        )

        metrics = RunMetrics(
            duration_seconds=duration_seconds,
            turns=turns,
            tokens_estimate=tokens_estimate,
            files_changed_count=len(changed_files),
            commands_run_count=len(commands),
            repair_attempts=repair_attempts,
            success=(status == "success"),
        )

        return RunResult(
            run_id=run_id,
            status=status,
            turns=turns,
            changed_files=changed_files,
            commands=commands,
            verification=verification,
            verification_results=verification_results,
            checkpoint_path=checkpoint_path,
            restore_plan_path=restore_plan_path,
            repair=repair,
            repair_attempts=repair_attempts,
            summary_path=str(summary_path.relative_to(self.workspace)),
            risk_summary=risk_summary,
            metrics=metrics,
        )
