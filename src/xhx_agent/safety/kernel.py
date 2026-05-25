from __future__ import annotations

from pathlib import Path
from typing import Callable

from xhx_agent.evidence.store import EvidenceStore, RawTraceEntry
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.safety.checkpoint import Checkpoint, CheckpointRestorePlan, create_checkpoint, create_restore_plan
from xhx_agent.safety.policy import PolicyDecision, decide_tool
from xhx_agent.tools.registry import ToolContext, ToolExecutionResult, ToolRegistry
from xhx_agent.tools.terminal import TerminalResult, run_terminal


ConfirmationCallback = Callable[[str, PolicyDecision], bool]


class SafeExecutionKernel:
    """Runtime-facing boundary for policy, execution, and audit writes."""

    def __init__(self, workspace: Path, run_id: str, evidence: EvidenceStore, tool_registry: ToolRegistry) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.evidence = evidence
        self.tool_registry = tool_registry

    def execute_tool(
        self,
        context: ToolContext,
        step,
        turn: int,
        event_callback: EventCallback | None = None,
    ) -> tuple[ToolExecutionResult | None, RawTraceEntry | None, PolicyDecision]:
        policy = decide_tool(step.tool)
        self.record_policy("tool", step.tool, policy, {"turn": turn, "tool": step.tool}, event_callback)
        if policy.decision == "deny":
            return None, None, policy
        trace = self.evidence.write_trace("tool_call", {"turn": turn, **step.model_dump()})
        result = self.tool_registry.execute(context, step)
        self.evidence.write_trace("tool_result", {"turn": turn, **result.trace_payload})
        return result, trace, policy

    def create_checkpoint(self, changed_files: list[str]) -> Checkpoint:
        checkpoint = create_checkpoint(self.workspace, self.run_id, sorted(set(changed_files)))
        self.evidence.write_trace("checkpoint", checkpoint.model_dump())
        self.evidence.write_evidence(
            "checkpoint",
            checkpoint.id,
            f"Checkpoint recorded {len(checkpoint.files)} changed file(s) before verification.",
            f"checkpoint://{checkpoint.id}",
            confidence=0.95,
        )
        return checkpoint

    def create_restore_plan(self, checkpoint: Checkpoint) -> CheckpointRestorePlan:
        plan = create_restore_plan(self.workspace, self.run_id, checkpoint)
        self.evidence.write_trace("restore_plan", plan.model_dump())
        changed = sum(1 for item in plan.files if item.status != "unchanged")
        self.evidence.write_evidence(
            "checkpoint",
            plan.id,
            f"Read-only restore plan recorded {changed} file(s) that differ from checkpoint metadata.",
            f"checkpoint://{plan.id}",
            confidence=0.85,
        )
        return plan

    def run_verification(
        self,
        command: str,
        assume_yes: bool,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
    ) -> TerminalResult:
        result = run_terminal(
            self.workspace,
            command,
            assume_yes=assume_yes,
            confirm_callback=confirm_callback,
        )
        self.record_policy("terminal", command, result.policy, {"command": command}, event_callback)
        self.evidence.write_trace("verification", result.model_dump())
        self.evidence.write_evidence(
            "test",
            command,
            _verification_evidence_summary(result),
            f"trace://{self.run_id}/verification/{command}",
            confidence=0.95 if result.status == "success" else 0.6,
        )
        return result

    def record_policy(
        self,
        scope: str,
        source: str,
        policy: PolicyDecision,
        payload: dict[str, object] | None = None,
        event_callback: EventCallback | None = None,
    ) -> None:
        trace_payload = {"scope": scope, **(payload or {}), **policy.model_dump(mode="json")}
        self.evidence.write_trace("policy_decision", trace_payload)
        emit_event(
            event_callback,
            "policy_decision",
            policy.reason,
            source=source,
            **trace_payload,
        )
        self.evidence.write_evidence(
            "policy",
            f"{scope}:{source}",
            f"{policy.decision}: {policy.reason}",
            f"trace://{self.run_id}/policy/{scope}/{source}",
            confidence=0.9,
        )


def _verification_evidence_summary(result: TerminalResult) -> str:
    exit_code = "none" if result.exit_code is None else str(result.exit_code)
    summary = result.summary or result.policy.reason
    return f"{result.status}: exit_code={exit_code}; {summary}"
