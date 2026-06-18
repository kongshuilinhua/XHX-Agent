from pathlib import Path

from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.models.types import ToolStep
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
    default_tool_registry,
)


def _registry_with_mcp_tool(read_only: bool = False) -> ToolRegistry:
    reg = ToolRegistry()

    def runner(ctx: ToolContext, args: dict) -> ToolExecutionResult:
        return ToolExecutionResult(tool="mcp_x_do", status="success", summary="did", trace_payload={"tool": "mcp_x_do"})

    reg.register_definition(
        ToolDefinition(
            name="mcp_x_do",
            description="dynamic mcp tool",
            parameters={"type": "object", "properties": {}},
            read_only=read_only,
            runner=runner,
        )
    )
    return reg


def test_kernel_records_policy_and_executes_tool(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result, trace, policy = kernel.execute_tool(
        ToolContext(workspace=tmp_path),
        ToolStep(tool="read_file", arguments={"path": "README.md"}),
        turn=1,
    )

    assert policy.decision == "allow"
    assert result is not None
    assert result.status == "success"
    assert trace is not None
    trace_text = evidence.trace_path.read_text(encoding="utf-8")
    evidence_text = evidence.evidence_path.read_text(encoding="utf-8")
    assert "policy_decision" in trace_text
    assert "tool_call" in trace_text
    assert "tool_result" in trace_text
    assert "tool:read_file" in evidence_text


def test_kernel_blocks_denied_tool(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result, trace, policy = kernel.execute_tool(
        ToolContext(workspace=tmp_path),
        ToolStep(tool="terminal", arguments={"command": "rm -rf /"}),
        turn=1,
    )

    # A denied tool never runs: no result, no tool_call trace.
    assert policy.decision == "deny"
    assert result is None
    assert trace is None

    # ...but the denial is still recorded as a policy decision, so the audit trail is complete.
    trace_text = evidence.trace_path.read_text(encoding="utf-8")
    assert "policy_decision" in trace_text
    assert "tool_call" not in trace_text


def test_run_command_tool_safe_runs(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result = kernel.run_command_tool("git status", evidence_kind="command", assume_yes=False, confirm_callback=None)

    assert result.tool == "terminal"
    # really executes; a non-git dir may fail but must not raise.
    assert result.status in ("success", "failed")


def test_run_command_tool_deny_blocked(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result = kernel.run_command_tool("rm -rf x", evidence_kind="command", assume_yes=False, confirm_callback=None)

    assert result.status == "deny"


def test_run_command_tool_confirm_declined(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result = kernel.run_command_tool(
        "pytest", evidence_kind="test", assume_yes=False, confirm_callback=lambda c, p: False
    )

    assert result.status == "confirm"


def test_kernel_read_only_phase_blocks_write_and_command(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())
    kernel.read_only_phase = True

    # Check read_file is read_only and should NOT be blocked
    (tmp_path / "README.md").write_text("hello\n", encoding="utf-8")
    result, trace, policy = kernel.execute_tool(
        ToolContext(workspace=tmp_path),
        ToolStep(tool="read_file", arguments={"path": "README.md"}),
        turn=1,
    )
    assert result is not None
    assert result.status == "success"

    # Check apply_patch is write and SHOULD be blocked
    result_patch, trace_patch, policy_patch = kernel.execute_tool(
        ToolContext(workspace=tmp_path),
        ToolStep(tool="apply_patch", arguments={"patch": "*** Begin Patch\n*** Add File: a.py\n+hi\n*** End Patch\n"}),
        turn=2,
    )
    assert result_patch is not None
    assert result_patch.status == "denied"
    assert "拦截" in result_patch.summary or "blocked" in result_patch.summary.lower() or "阶段" in result_patch.summary

    # Check command run is blocked
    result_cmd = kernel.run_command_tool("pytest", turn=3)
    assert result_cmd.status == "denied"
    assert "拦截" in result_cmd.summary or "blocked" in result_cmd.summary.lower() or "阶段" in result_cmd.summary

    # run_verification 在只读阶段也应被拦截，且不能因 TerminalResult 缺 command 而崩溃（回归）
    result_verify = kernel.run_verification("pytest", assume_yes=True)
    assert result_verify.status == "deny"
    assert result_verify.command == "pytest"


def _exec_mcp(tmp_path: Path, *, read_only=False, permission_mode="default", confirm=None, assume_yes=False):
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, _registry_with_mcp_tool(read_only=read_only))
    return kernel.execute_tool(
        ToolContext(workspace=tmp_path, permission_mode=permission_mode),
        ToolStep(tool="mcp_x_do", arguments={"k": "v"}),
        turn=1,
        confirm_callback=confirm,
        assume_yes=assume_yes,
    )


def test_mcp_tool_confirm_approved(tmp_path: Path) -> None:
    calls = []
    result, _trace, policy = _exec_mcp(tmp_path, confirm=lambda p, d: calls.append(p) or True)
    assert calls  # 弹框被触发
    assert result is not None and result.status == "success"


def test_mcp_tool_confirm_declined(tmp_path: Path) -> None:
    result, _trace, policy = _exec_mcp(tmp_path, confirm=lambda p, d: False)
    assert result is not None and result.status == "denied"
    assert policy.decision == "deny"


def test_mcp_tool_assume_yes_skips_confirm(tmp_path: Path) -> None:
    calls = []
    result, _trace, _policy = _exec_mcp(tmp_path, confirm=lambda p, d: calls.append(p) or True, assume_yes=True)
    assert not calls  # 预批 → 不弹框
    assert result is not None and result.status == "success"


def test_mcp_tool_bypass_skips_confirm(tmp_path: Path) -> None:
    calls = []
    result, _trace, _policy = _exec_mcp(
        tmp_path, permission_mode="bypass", confirm=lambda p, d: calls.append(p) or True
    )
    assert not calls
    assert result is not None and result.status == "success"


def test_mcp_tool_unattended_default_denies(tmp_path: Path) -> None:
    # 无回调、default 模式、未预批 → 安全默认拒绝
    result, _trace, policy = _exec_mcp(tmp_path, confirm=None, assume_yes=False)
    assert result is not None and result.status == "denied"
    assert policy.decision == "deny"


def test_mcp_readonly_tool_no_confirm(tmp_path: Path) -> None:
    calls = []
    result, _trace, _policy = _exec_mcp(tmp_path, read_only=True, confirm=lambda p, d: calls.append(p) or True)
    assert not calls  # 只读 MCP 工具不弹框
    assert result is not None and result.status == "success"
