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


def test_kernel_out_of_scope_read_default_allow(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    ext_file = external / "README.md"
    ext_file.write_text("external content\n", encoding="utf-8")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())

    context = ToolContext(workspace=workspace, allowed_dirs=[], permission_mode="default")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    # Mock confirm_callback that returns True
    confirm_called = []

    def confirm_cb(prompt: str, policy) -> bool:
        confirm_called.append((prompt, policy))
        return True

    result, trace, policy = kernel.execute_tool(
        context,
        step,
        turn=1,
        confirm_callback=confirm_cb,
    )

    assert len(confirm_called) == 1
    assert "允许读取工作区外目录" in confirm_called[0][0]
    assert str(external.resolve()) in confirm_called[0][0]
    assert result is not None
    assert result.status == "success"
    # Target directory should be added to allowed_dirs
    assert Path(external).resolve() in context.allowed_dirs

    # A second read should NOT trigger the callback
    confirm_called.clear()
    result2, trace2, policy2 = kernel.execute_tool(
        context,
        step,
        turn=2,
        confirm_callback=confirm_cb,
    )
    assert len(confirm_called) == 0
    assert result2.status == "success"


def test_kernel_out_of_scope_read_default_deny(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    ext_file = external / "README.md"

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())

    context = ToolContext(workspace=workspace, allowed_dirs=[], permission_mode="default")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    def confirm_cb(prompt: str, policy) -> bool:
        return False

    result, trace, policy = kernel.execute_tool(
        context,
        step,
        turn=1,
        confirm_callback=confirm_cb,
    )

    assert result is not None
    assert result.status == "denied"
    assert "用户拒绝访问工作区外路径" in result.summary


def test_kernel_out_of_scope_read_auto(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    ext_file = external / "README.md"
    ext_file.write_text("external content\n", encoding="utf-8")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())

    context = ToolContext(workspace=workspace, allowed_dirs=[], permission_mode="auto")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    confirm_called = []

    def confirm_cb(prompt: str, policy) -> bool:
        confirm_called.append(prompt)
        return True

    result, trace, policy = kernel.execute_tool(
        context,
        step,
        turn=1,
        confirm_callback=confirm_cb,
    )

    # In auto mode, read is automatically allowed, confirm_callback is not called
    assert len(confirm_called) == 0
    assert result.status == "success"
    # Target directory should be added to allowed_dirs
    assert Path(external).resolve() in context.allowed_dirs


def test_kernel_out_of_scope_write_auto_confirm(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    ext_file = external / "patch.txt"

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())

    context = ToolContext(workspace=workspace, allowed_dirs=[], permission_mode="auto")
    # Write operation
    patch_content = f"--- /dev/null\n+++ {ext_file.as_posix()}\n@@ -0,0 +1,1 @@\n+hello\n"
    step = ToolStep(tool="apply_patch", arguments={"patch": patch_content})

    confirm_called = []

    def confirm_cb(prompt: str, policy) -> bool:
        confirm_called.append(prompt)
        return True

    result, trace, policy = kernel.execute_tool(
        context,
        step,
        turn=1,
        confirm_callback=confirm_cb,
    )

    # Write in auto mode still triggers confirm_callback (safety baseline)
    assert len(confirm_called) == 1
    assert "允许修改工作区外目录" in confirm_called[0]
    assert result.status == "success"


def test_kernel_out_of_scope_assume_yes_deny(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    ext_file = external / "README.md"

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())

    # assume_yes=True, but confirm_callback is None, permission_mode is default
    context = ToolContext(workspace=workspace, allowed_dirs=[], permission_mode="default")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    result, trace, policy = kernel.execute_tool(
        context,
        step,
        turn=1,
        confirm_callback=None,  # no callback
    )

    # Should be denied automatically without blocking
    assert result is not None
    assert result.status == "denied"


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
