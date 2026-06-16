"""安全执行内核：运行时调用工具 / 跑验证命令 / 写审计的唯一入口。

把「策略判定 → 执行 → 证据落盘」收口在一处：每次工具调用先经 policy 判定，deny 的直接短路
（但仍记一条 policy_decision，保证审计链不断），其余才真正执行并写 trace/evidence。

自 mewcode 集成后，路径越界检测委托给 PathSandbox，权限判定可通过 PermissionChecker 五层检查。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from xhx_agent.evidence.store import EvidenceStore, RawTraceEntry
from xhx_agent.runtime.events import EventCallback, emit_event
from xhx_agent.safety.checkpoint import Checkpoint, CheckpointRestorePlan, create_checkpoint, create_restore_plan
from xhx_agent.safety.policy import PolicyDecision, decide_tool, decide_with_checker
from xhx_agent.safety.risk import RiskLevel
from xhx_agent.tools.paths import extract_glob_root, resolve_with_scope
from xhx_agent.tools.registry import ToolContext, ToolExecutionResult, ToolRegistry
from xhx_agent.tools.terminal import TerminalResult, run_terminal

if TYPE_CHECKING:
    from xhx_agent.safety.permissions.checker import PermissionChecker
    from xhx_agent.safety.permissions.sandbox import PathSandbox

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
        self.permission_checker = permission_checker
        self.path_sandbox = path_sandbox

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

        # 1. 策略判定
        policy = decide_tool(
            step.tool,
            read_only=is_read_only,
            destructive=bool(d and d.destructive),
            network=bool(d and d.network),
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

        # 2. 路径越界预检与授权裁决（优先使用 PathSandbox）
        if self.path_sandbox is not None:
            out_of_scope = self._check_paths_with_sandbox(step)
            if out_of_scope:
                result = self._handle_path_denial(
                    step, out_of_scope, context, turn, is_read_only, confirm_callback, event_callback
                )
                if result is not None:
                    return result
        else:
            # 回退：使用旧 resolve_with_scope 路径
            out_of_scope_paths = _collect_out_of_scope_paths(step, context)
            if out_of_scope_paths:
                result = _handle_out_of_scope_paths(
                    self, step, out_of_scope_paths, context, turn, is_read_only, confirm_callback, event_callback
                )
                if result is not None:
                    return result

        # 3. 正常执行
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


    # ------------------------------------------------------------------
    # path sandbox helpers (mewcode integration)
    # ------------------------------------------------------------------

    def _check_paths_with_sandbox(self, step: object) -> list[str]:
        """使用 PathSandbox 检查工具调用中的路径。返回越界路径列表。"""
        assert self.path_sandbox is not None
        out_of_scope: list[str] = []

        paths = _extract_tool_paths(step)
        for p in paths:
            ok, _reason = self.path_sandbox.check(p)
            if not ok:
                out_of_scope.append(p)
        return out_of_scope

    def _handle_path_denial(
        self,
        step: object,
        out_of_scope: list[str],
        context: ToolContext,
        turn: int,
        is_read_only: bool,
        confirm_callback: ConfirmationCallback | None,
        event_callback: EventCallback | None,
    ) -> tuple[ToolExecutionResult | None, RawTraceEntry | None, PolicyDecision] | None:
        """处理 PathSandbox 检测到的越界路径。返回 None 表示已放行，否则返回拒绝结果。"""
        mode = context.permission_mode or "default"

        for path_str in out_of_scope:
            resolved = Path(path_str).resolve()

            # bypass → 自动添加允许根
            if mode == "bypass":
                assert self.path_sandbox is not None
                self.path_sandbox.add_allowed_root(str(resolved))
                self.record_policy(
                    "path_scope", str(resolved),
                    PolicyDecision(decision="allow", risk=RiskLevel.SAFE,
                                   reason=f"Auto-allowed out-of-scope path under bypass mode"),
                    {"turn": turn, "tool": step.tool, "path": str(resolved)},
                    event_callback,
                )
                continue

            # auto → 只读放行
            if mode == "auto" and is_read_only:
                assert self.path_sandbox is not None
                self.path_sandbox.add_allowed_root(str(resolved))
                self.record_policy(
                    "path_scope", str(resolved),
                    PolicyDecision(decision="allow", risk=RiskLevel.SAFE,
                                   reason=f"Auto-allowed out-of-scope read path under auto mode"),
                    {"turn": turn, "tool": step.tool, "path": str(resolved)},
                    event_callback,
                )
                continue

            # default / 其他 → 弹框确认
            action = "修改" if not is_read_only else "读取"
            prompt = f"允许{action}工作区外目录?\n{resolved}"
            decision_p = PolicyDecision(
                decision="confirm", risk=RiskLevel.CONFIRM,
                reason=f"Path is outside workspace under {mode} mode",
                requires_user=True,
            )

            confirmed = False
            if confirm_callback is not None:
                confirmed = confirm_callback(prompt, decision_p)

            if confirmed:
                assert self.path_sandbox is not None
                self.path_sandbox.add_allowed_root(str(resolved))
                self.record_policy(
                    "path_scope", str(resolved),
                    PolicyDecision(decision="allow", risk=RiskLevel.SAFE,
                                   reason="User allowed out-of-scope path access"),
                    {"turn": turn, "tool": step.tool, "path": str(resolved)},
                    event_callback,
                )
            else:
                reason = f"用户拒绝访问工作区外路径: {resolved}"
                self.record_policy(
                    "path_scope", str(resolved),
                    PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason),
                    {"turn": turn, "tool": step.tool, "path": str(resolved)},
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

        return None  # 全部放行


# ---------------------------------------------------------------------------
# 独立辅助函数
# ---------------------------------------------------------------------------


def _extract_tool_paths(step: object) -> list[str]:
    """从工具参数中提取所有文件路径（供 PathSandbox 检查）。"""
    paths: list[str] = []

    if step.tool == "read_file":
        p = step.arguments.get("path")
        if p:
            paths.append(str(p))
    elif step.tool == "search":
        g = step.arguments.get("glob")
        if g:
            paths.append(str(g))
    elif step.tool == "apply_patch":
        patch_arg = step.arguments.get("patch")
        if patch_arg:
            try:
                from xhx_agent.tools.patch import _parse_patch
                for op in _parse_patch(str(patch_arg)):
                    paths.append(op.path)
            except Exception:
                pass

    return paths


def _collect_out_of_scope_paths(step: object, context: ToolContext) -> list[object]:
    """旧路径越界检测（resolve_with_scope），在无 PathSandbox 时回退使用。"""
    out_of_scope: list[object] = []

    if step.tool == "read_file":
        path_arg = step.arguments.get("path")
        if path_arg:
            res = resolve_with_scope(context.workspace, context.allowed_dirs, path_arg)
            if not res.in_scope:
                out_of_scope.append(res)
    elif step.tool == "search":
        glob_arg = step.arguments.get("glob")
        if glob_arg:
            glob_root = extract_glob_root(context.workspace, glob_arg)
            res = resolve_with_scope(context.workspace, context.allowed_dirs, glob_root)
            if not res.in_scope:
                out_of_scope.append(res)
    elif step.tool == "apply_patch":
        patch_arg = step.arguments.get("patch")
        if patch_arg:
            try:
                from xhx_agent.tools.patch import _parse_patch
                for op in _parse_patch(str(patch_arg)):
                    res = resolve_with_scope(context.workspace, context.allowed_dirs, op.path)
                    if not res.in_scope:
                        out_of_scope.append(res)
            except Exception:
                pass

    return out_of_scope


def _handle_out_of_scope_paths(
    kernel: SafeExecutionKernel,
    step: object,
    out_of_scope_paths: list[object],
    context: ToolContext,
    turn: int,
    is_read_only: bool,
    confirm_callback: ConfirmationCallback | None,
    event_callback: EventCallback | None,
) -> tuple[ToolExecutionResult | None, RawTraceEntry | None, PolicyDecision] | None:
    """旧越界裁决逻辑，在无 PathSandbox 时回退使用。返回 None = 全部放行。"""
    mode = context.permission_mode or "default"
    for path_scope in out_of_scope_paths:
        outside_root = path_scope.outside_root
        if not outside_root:
            continue
        outside_root_resolved = Path(outside_root).resolve()

        allowed = False
        if mode == "bypass":
            allowed = True
        elif mode == "auto":
            allowed = is_read_only  # auto: 只读自动放行，写需要确认
        else:
            allowed = False

        if allowed:
            if outside_root_resolved not in context.allowed_dirs:
                context.allowed_dirs.append(outside_root_resolved)
            kernel.record_policy(
                "path_scope", str(outside_root_resolved),
                PolicyDecision(decision="allow", risk=RiskLevel.SAFE,
                               reason=f"Auto-allowed out-of-scope path under {mode} mode"),
                {"turn": turn, "tool": step.tool, "path": str(outside_root_resolved)},
                event_callback,
            )
        else:
            action = "修改" if not is_read_only else "读取"
            prompt = f"允许{action}工作区外目录?\n{outside_root_resolved}"
            decision_p = PolicyDecision(
                decision="confirm", risk=RiskLevel.CONFIRM,
                reason=f"Path is outside workspace under {mode} mode",
                requires_user=True,
            )
            confirmed = False
            if confirm_callback is not None:
                confirmed = confirm_callback(prompt, decision_p)

            if confirmed:
                if outside_root_resolved not in context.allowed_dirs:
                    context.allowed_dirs.append(outside_root_resolved)
                kernel.record_policy(
                    "path_scope", str(outside_root_resolved),
                    PolicyDecision(decision="allow", risk=RiskLevel.SAFE,
                                   reason="User allowed out-of-scope path access"),
                    {"turn": turn, "tool": step.tool, "path": str(outside_root_resolved)},
                    event_callback,
                )
            else:
                reason = f"用户拒绝访问工作区外路径: {outside_root_resolved}"
                kernel.record_policy(
                    "path_scope", str(outside_root_resolved),
                    PolicyDecision(decision="deny", risk=RiskLevel.DENY, reason=reason),
                    {"turn": turn, "tool": step.tool, "path": str(outside_root_resolved)},
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

    return None


def _verification_evidence_summary(result: TerminalResult) -> str:
    exit_code = "none" if result.exit_code is None else str(result.exit_code)
    summary = result.summary or result.policy.reason
    return f"{result.status}: exit_code={exit_code}; {summary}"
