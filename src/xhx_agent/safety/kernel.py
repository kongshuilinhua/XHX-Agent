"""安全执行内核：运行时调用工具 / 跑验证命令 / 写审计的唯一入口。

把「策略判定 → 执行 → 证据落盘」收口在一处：每次工具调用先经 policy 判定，deny 的直接短路
（但仍记一条 policy_decision，保证审计链不断），其余才真正执行并写 trace/evidence。

自 mewcode 集成后，路径越界检测委托给 PathSandbox，权限判定可通过 PermissionChecker 五层检查。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from xhx_agent.evidence.store import EvidenceStore, RawTraceEntry
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.safety.checkpoint import Checkpoint, CheckpointRestorePlan, create_checkpoint, create_restore_plan
from xhx_agent.safety.permissions.dangerous import DangerousCommandDetector
from xhx_agent.safety.permissions.checker import PermissionChecker
from xhx_agent.safety.permissions.rules import RuleEngine
from xhx_agent.safety.permissions.sandbox import PathSandbox
from xhx_agent.safety.policy import PolicyDecision, decide_with_checker
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.registry import ToolContext, ToolExecutionResult, ToolRegistry
from xhx_agent.tools.terminal import TerminalResult, run_terminal

ConfirmationCallback = Callable[[str, PolicyDecision], bool]


class SafeExecutionKernel:
    """运行时面向的边界：把策略判定、工具执行、审计写入收口在一起。"""

    def __init__(
        self,
        workspace: Path,
        run_id: str,
        evidence: EvidenceStore,
        tool_registry: ToolRegistry,
        *,
        permission_checker: PermissionChecker | None = None,
        path_sandbox: PathSandbox | None = None,
    ) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.evidence = evidence
        self.tool_registry = tool_registry
        self.read_only_phase: bool = False

        # Auto-create PathSandbox + PermissionChecker if not provided
        if path_sandbox is None:
            path_sandbox = PathSandbox(str(workspace))
        self.path_sandbox = path_sandbox

        if permission_checker is None:
            permissions_file = workspace / ".xhx" / "permissions.yaml"
            rule_engine = RuleEngine(
                project_rules_path=permissions_file if permissions_file.is_file() else None,
            )
            permission_checker = PermissionChecker(
                detector=DangerousCommandDetector(),
                sandbox=path_sandbox,
                rule_engine=rule_engine,
            )
        self.permission_checker = permission_checker

    def execute_tool(
        self,
        context: ToolContext,
        step,
        turn: int,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
        assume_yes: bool = False,
    ) -> tuple[ToolExecutionResult | None, RawTraceEntry | None, PolicyDecision]:
        d = self.tool_registry.definition(step.tool)
        is_read_only = bool(d and d.read_only)

        # 0. 只读规划阶段硬拦任何写/命令工具
        if self.read_only_phase and not is_read_only:
            reason = f"工具 {step.tool} 在计划规划(只读)阶段被拦截"
            self.record_policy(
                "tool",
                step.tool,
                PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason),
                {"turn": turn, "tool": step.tool},
                event_callback,
            )
            return (
                ToolExecutionResult(
                    tool=step.tool,
                    status="denied",
                    summary=reason,
                    trace_payload={"tool": step.tool, "status": "denied", "error": reason},
                ),
                None,
                PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason),
            )

        # 1. 策略判定（通过 PermissionChecker 五层检查）
        is_destructive = bool(d and d.destructive)
        is_network = bool(d and d.network)
        tool_category = "read" if is_read_only else ("write" if is_destructive else "command")

        policy = decide_with_checker(
            step.tool,
            step.arguments,
            self.permission_checker,
            tool_category=tool_category,
            read_only=is_read_only,
            destructive=is_destructive,
            network=is_network,
        )
        self.record_policy("tool", step.tool, policy, {"turn": turn, "tool": step.tool}, event_callback)
        if policy.decision == "deny":
            # 被拒工具到此为止：不产生 tool_call、不执行；但上面已记 policy_decision，审计链完整。
            return None, None, policy

        # 1b. 动态外部工具（mcp_/custom_ 非只读）确认门：以 agent 权限运行、无沙箱，可对外部系统产生
        #     副作用（如 GitHub 写）。bypass / assume_yes(-y) 放行；否则有回调就弹框确认，无回调且未预批
        #     则安全默认拒绝（不静默执行外部副作用）。
        if policy.requires_user:
            mode = context.permission_mode or "default"
            if mode != "bypass" and not assume_yes:
                approved = False
                if confirm_callback is not None:
                    prompt = f"允许执行外部工具 {step.tool}?\n参数: {step.arguments}"
                    approved = confirm_callback(prompt, policy)
                if not approved:
                    reason = (
                        f"用户拒绝执行外部工具: {step.tool}"
                        if confirm_callback is not None
                        else f"外部工具 {step.tool} 需确认，但无人值守且未预批，已拒绝"
                    )
                    denied = PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason)
                    self.record_policy("tool", step.tool, denied, {"turn": turn, "tool": step.tool}, event_callback)
                    return (
                        ToolExecutionResult(
                            tool=step.tool,
                            status="denied",
                            summary=reason,
                            trace_payload={"tool": step.tool, "status": "denied", "error": reason},
                        ),
                        None,
                        denied,
                    )

        # 2. 正常执行（路径越界检查由 PermissionChecker Layer 2 处理）
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
        if self.read_only_phase:
            policy = PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason="Blocked in read-only phase")
            result = TerminalResult(
                command=command,
                status="deny",
                exit_code=None,
                stdout="",
                stderr="",
                policy=policy,
                summary="Blocked in read-only phase",
            )
            self.record_policy("terminal", command, policy, {"command": command}, event_callback)
            self.evidence.write_trace("verification", result.model_dump())
            return result

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

    def run_command_tool(
        self,
        command: str,
        *,
        evidence_kind: str = "command",
        assume_yes: bool = False,
        confirm_callback: ConfirmationCallback | None = None,
        event_callback: EventCallback | None = None,
        turn: int = 0,
    ) -> ToolExecutionResult:
        """命令工具（terminal/verify）的执行入口：过 decide_terminal 命令级闸门 + confirm，跑命令，转成 ToolExecutionResult。"""
        if self.read_only_phase:
            reason = f"命令执行在计划规划(只读)阶段被拦截: {command}"
            self.record_policy(
                "terminal",
                command,
                PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason),
                {"turn": turn, "command": command},
                event_callback,
            )
            return ToolExecutionResult(
                tool="terminal",
                status="denied",
                summary=reason,
                trace_payload={"tool": "terminal", "command": command, "status": "denied", "error": reason},
                error=reason,
            )

        result = run_terminal(self.workspace, command, assume_yes=assume_yes, confirm_callback=confirm_callback)
        self.record_policy("terminal", command, result.policy, {"turn": turn, "command": command}, event_callback)
        self.evidence.write_trace(
            "tool_result", {"turn": turn, "tool": "terminal", "command": command, **result.model_dump()}
        )
        ok = result.status == "success"
        return ToolExecutionResult(
            tool="terminal",
            status=result.status,
            summary=result.summary or f"command {result.status}",
            trace_payload={"tool": "terminal", "command": command, **result.model_dump()},
            evidence_kind=evidence_kind if ok else None,
            evidence_source=command if ok else None,
            evidence_summary=result.summary if ok else None,
            error=None if ok else (result.stderr or result.summary or result.status),
        )

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
