from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from xhx_agent.runtime.events import RuntimeEvent

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


@dataclass
class ToolActivity:
    tool: str
    turn: int | None = None
    status: str = "running"
    summary: str = ""


@dataclass
class VerificationActivity:
    command: str
    status: str = "running"
    exit_code: int | None = None


@dataclass
class PolicyActivity:
    scope: str
    source: str
    decision: str
    risk: str
    reason: str
    requires_user: bool = False


@dataclass
class ConsoleState:
    """Small reducer-owned state used by the v0.5 command console."""

    status: str = "idle"
    run_id: str | None = None
    task: str | None = None
    profile: str | None = None
    mode: str = "linear-edit"
    detected_languages: list[str] = field(default_factory=list)
    file_count: int = 0
    plan_summary: str | None = None
    plan_status: str | None = None
    plan_step_count: int = 0
    model_output: str = ""
    model_delta_count: int = 0
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    context_turn: int | None = None
    context_selected: int = 0
    context_omitted: int = 0
    context_used_tokens_estimate: int = 0
    context_budget_tokens: int = 0
    changed_files: list[str] = field(default_factory=list)
    tools: list[ToolActivity] = field(default_factory=list)
    verification: str = "not_started"
    verifications: list[VerificationActivity] = field(default_factory=list)
    repair_attempts: int = 0
    repair_max_attempts: int = 0
    repair_reason: str = ""
    restore_plan_created: bool = False
    summary_path: str | None = None
    events: list[RuntimeEvent] = field(default_factory=list)
    policy_decisions: list[PolicyActivity] = field(default_factory=list)
    cancel_requested: bool = False
    cancel_reason: str = ""
    is_streaming: bool = False
    answer: str | None = None

    def reduce(self, event: RuntimeEvent) -> None:
        if event.type == "run_start":
            self._reset_for_run(event)
        else:
            self.events.append(event)

        if event.type == "model_delta":
            self.is_streaming = True
        elif event.type in {
            "model_plan",
            "tool_start",
            "verification_start",
            "repair_start",
            "run_end",
            "run_cancelled",
            "cancel_requested",
        }:
            self.is_streaming = False

        payload = event.payload
        if payload and "turn" in payload:
            t_val = _optional_int(payload.get("turn"))
            if t_val is not None:
                self.context_turn = t_val

        if event.type == "scan":
            self.detected_languages = [str(item) for item in payload.get("detected_languages", [])]
            self.file_count = int(payload.get("file_count", 0) or 0)
        elif event.type == "context_pack":
            self.status = "planning"
            self.context_turn = _optional_int(payload.get("turn"))
            self.context_selected = int(payload.get("selected", 0) or 0)
            self.context_omitted = int(payload.get("omitted", 0) or 0)
            self.context_used_tokens_estimate = int(payload.get("used_tokens_estimate", 0) or 0)
            self.context_budget_tokens = int(payload.get("budget_tokens", 0) or 0)
        elif event.type == "model_plan_start":
            self.status = "planning"
        elif event.type == "model_delta":
            self.status = "planning"
            self.model_delta_count += 1
            self.model_output = _trim_model_output(self.model_output + event.message)
        elif event.type == "token_usage":
            self.tokens_prompt = int(payload.get("prompt", self.tokens_prompt) or 0)
            self.tokens_completion = int(payload.get("completion", self.tokens_completion) or 0)
            self.tokens_total = int(payload.get("cumulative_total", self.tokens_total) or 0)
        elif event.type == "model_plan":
            self.status = "planning"
            self.plan_summary = event.message
            self.plan_status = str(payload.get("status", ""))
            self.plan_step_count = int(payload.get("step_count", 0) or 0)
        elif event.type == "tool_start":
            self.status = "running_tool"
            self.tools.append(
                ToolActivity(
                    tool=str(payload.get("tool", "unknown")),
                    turn=_optional_int(payload.get("turn")),
                )
            )
        elif event.type == "policy_decision":
            policy = PolicyActivity(
                scope=str(payload.get("scope", "")),
                source=str(payload.get("source", "")),
                decision=str(payload.get("decision", "")),
                risk=str(payload.get("risk", "")),
                reason=str(payload.get("reason", "")),
                requires_user=bool(payload.get("requires_user", False)),
            )
            self.policy_decisions.append(policy)
            if policy.requires_user or policy.decision == "confirm":
                self.status = "waiting_confirmation"
            elif policy.decision == "deny":
                self.status = "failed"
        elif event.type == "tool_result":
            self._update_tool(event)
        elif event.type == "checkpoint":
            self.changed_files = _merge_unique(
                self.changed_files, [str(item) for item in payload.get("changed_files", [])]
            )
        elif event.type == "verification_start":
            self.status = "verifying"
            command = str(payload.get("command", ""))
            self.verifications.append(VerificationActivity(command=command))
            self.verification = "running"
        elif event.type == "verification_result":
            self._update_verification(event)
        elif event.type == "repair_decision":
            self.repair_reason = event.message
            self.repair_attempts = int(payload.get("attempts_used", self.repair_attempts) or 0)
            self.repair_max_attempts = int(payload.get("max_attempts", self.repair_max_attempts) or 0)
        elif event.type == "repair_start":
            self.status = "repairing"
            self.repair_attempts = int(payload.get("attempt", self.repair_attempts) or 0)
            self.repair_max_attempts = int(payload.get("max_attempts", self.repair_max_attempts) or 0)
        elif event.type == "restore_plan":
            self.restore_plan_created = True
        elif event.type == "cancel_requested":
            self.status = "cancelling"
            self.cancel_requested = True
            self.cancel_reason = event.message
        elif event.type == "run_cancelled":
            self.status = "cancelled"
            self.cancel_requested = True
            self.cancel_reason = event.message
            self.verification = "cancelled"
        elif event.type == "run_end":
            self.status = str(payload.get("status", "finished"))
            self.verification = str(payload.get("verification", self.verification))
            self.summary_path = str(payload.get("summary_path", "")) or self.summary_path
            self.changed_files = _merge_unique(
                self.changed_files, [str(item) for item in payload.get("changed_files", [])]
            )

    def apply_result(self, result: RunResult) -> None:
        self.status = result.status
        self.run_id = result.run_id
        self.changed_files = list(result.changed_files)
        self.verification = result.verification
        self.summary_path = result.summary_path
        self.repair_attempts = result.repair_attempts
        self.answer = getattr(result, "answer", None)
        if result.verification_results:
            self.verifications = [
                VerificationActivity(command=item.command, status=item.status, exit_code=item.exit_code)
                for item in result.verification_results
            ]

    def _reset_for_run(self, event: RuntimeEvent) -> None:
        previous_mode = self.mode
        self.__dict__.update(ConsoleState(mode=previous_mode).__dict__)
        self.events.append(event)
        self.status = "running"
        self.run_id = str(event.payload.get("run_id", "")) or None
        self.task = str(event.payload.get("task", "")) or None
        self.profile = str(event.payload.get("profile", "")) or None

    def _update_tool(self, event: RuntimeEvent) -> None:
        tool = str(event.payload.get("tool", "unknown"))
        turn = _optional_int(event.payload.get("turn"))
        for item in reversed(self.tools):
            if item.tool == tool and (turn is None or item.turn == turn) and item.status == "running":
                item.status = str(event.payload.get("status", "finished"))
                item.summary = str(event.payload.get("summary", event.message))
                break
        else:
            self.tools.append(
                ToolActivity(
                    tool=tool,
                    turn=turn,
                    status=str(event.payload.get("status", "finished")),
                    summary=str(event.payload.get("summary", event.message)),
                )
            )
        self.status = "running"

    def _update_verification(self, event: RuntimeEvent) -> None:
        command = str(event.payload.get("command", ""))
        status = str(event.payload.get("status", "finished"))
        exit_code = _optional_int(event.payload.get("exit_code"))
        for item in reversed(self.verifications):
            if item.command == command and item.status == "running":
                item.status = status
                item.exit_code = exit_code
                break
        else:
            self.verifications.append(VerificationActivity(command=command, status=status, exit_code=exit_code))
        self.verification = status
        self.status = "waiting_confirmation" if status == "confirm" else "verifying"


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = list(existing)
    for item in incoming:
        if item and item not in merged:
            merged.append(item)
    return merged


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float, str, bytes)):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def _trim_model_output(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]
