"""PermissionChecker integration tests — replaces old path-scope tests."""

from pathlib import Path

from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.models.types import ToolStep
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import ToolContext, default_tool_registry


def test_path_sandbox_denies_out_of_scope(tmp_path: Path) -> None:
    """PermissionChecker Layer 2 blocks paths outside workspace."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Use a path outside temp dir (tempfile.gettempdir is auto-allowed by PathSandbox)
    ext_file = Path("C:/outside/workspace/README.md")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())
    context = ToolContext(workspace=workspace, permission_mode="default")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    result, _trace, policy = kernel.execute_tool(context, step, turn=1)
    assert policy.decision == "deny"
    assert result is None


def test_path_sandbox_allows_in_scope(tmp_path: Path) -> None:
    """In-scope paths pass through PermissionChecker."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    in_file = workspace / "in.md"
    in_file.write_text("content\n", encoding="utf-8")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())
    context = ToolContext(workspace=workspace, permission_mode="default")
    step = ToolStep(tool="read_file", arguments={"path": str(in_file)})

    result, _trace, policy = kernel.execute_tool(context, step, turn=1)
    assert policy.decision == "allow"
    assert result is not None
    assert result.status == "success"


def test_path_sandbox_bypass_allows_out_of_scope(tmp_path: Path) -> None:
    """Bypass permission mode grants out-of-scope access."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # Create the file to avoid FileNotFoundError after policy passes
    ext_dir = tmp_path.parent / "outside_workspace"
    ext_dir.mkdir(exist_ok=True)
    ext_file = ext_dir / "README.md"
    ext_file.write_text("bypass test\n", encoding="utf-8")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())
    context = ToolContext(workspace=workspace, permission_mode="bypass")
    step = ToolStep(tool="read_file", arguments={"path": str(ext_file)})

    result, _trace, policy = kernel.execute_tool(context, step, turn=1)
    assert policy.decision == "allow"
    assert result is not None
    assert result.status == "success"


def test_permission_checker_blocks_destructive_out_of_scope(tmp_path: Path) -> None:
    """Destructive tool on out-of-scope path is denied."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ext_file = Path("C:/outside/workspace/config.yaml")

    evidence = EvidenceStore(workspace, "run-test")
    kernel = SafeExecutionKernel(workspace, "run-test", evidence, default_tool_registry())
    context = ToolContext(workspace=workspace, permission_mode="default")
    step = ToolStep(
        tool="apply_patch", arguments={"patch": f"*** Begin Patch\n*** Add File: {ext_file}\n+hello\n*** End Patch\n"}
    )

    result, _trace, policy = kernel.execute_tool(context, step, turn=1)
    assert policy.decision == "deny"
    assert result is None
