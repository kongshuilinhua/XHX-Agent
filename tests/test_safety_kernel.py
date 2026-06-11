from pathlib import Path

from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.models.types import ToolStep
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import ToolContext, default_tool_registry


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

    result = kernel.run_command_tool(
        "git status", evidence_kind="command", assume_yes=False, confirm_callback=None
    )

    assert result.tool == "terminal"
    # really executes; a non-git dir may fail but must not raise.
    assert result.status in ("success", "failed")


def test_run_command_tool_deny_blocked(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result = kernel.run_command_tool(
        "rm -rf x", evidence_kind="command", assume_yes=False, confirm_callback=None
    )

    assert result.status == "deny"


def test_run_command_tool_confirm_declined(tmp_path: Path) -> None:
    evidence = EvidenceStore(tmp_path, "run-test")
    kernel = SafeExecutionKernel(tmp_path, "run-test", evidence, default_tool_registry())

    result = kernel.run_command_tool(
        "pytest", evidence_kind="test", assume_yes=False, confirm_callback=lambda c, p: False
    )

    assert result.status == "confirm"
