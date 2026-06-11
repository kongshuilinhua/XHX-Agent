from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.models.types import ToolCall
from xhx_agent.orchestrators._toolturn import execute_tool_call
from xhx_agent.orchestrators.base import OrchestratorContext
from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.tools.registry import ToolContext


# Test that execute_tool_call works as before (3-tuple contract)
def test_execute_tool_call_basic(tmp_path):
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()
    evidence = EvidenceStore(tmp_path, "run-1")
    kernel = SafeExecutionKernel(tmp_path, "run-1", evidence, app.tool_registry)
    tool_context = ToolContext(workspace=tmp_path, max_file_bytes=100000)

    ctx = OrchestratorContext(
        app=app,
        task="write patch",
        run_id="run-1",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=None,
        scan=None,
        evidence=evidence,
        kernel=kernel,
        tool_context=tool_context,
        start_time=0.0,
        isolated=False,
    )

    patch_content = (
        "*** Begin Patch\n"
        "*** Update File: README.md\n"
        "@@\n"
        "-# hello\n"
        "+# hello world\n"
        "*** End Patch\n"
    )
    tc = ToolCall(id="c1", name="apply_patch", arguments={"patch": patch_content})

    tc_res, content, changed = execute_tool_call(ctx, tc, turn=1)
    assert tc_res.id == "c1"
    assert "README.md" in content
    assert changed == ["README.md"]

# Test that the new rich execution function returns meta info
def test_rich_returns_patch_evidence_meta(tmp_path):
    from xhx_agent.orchestrators._toolturn import _execute_tool_call_rich

    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()
    evidence = EvidenceStore(tmp_path, "run-1")
    kernel = SafeExecutionKernel(tmp_path, "run-1", evidence, app.tool_registry)
    tool_context = ToolContext(workspace=tmp_path, max_file_bytes=100000)

    ctx = OrchestratorContext(
        app=app,
        task="write patch",
        run_id="run-1",
        workspace=tmp_path,
        original_workspace=tmp_path,
        profile=None,
        scan=None,
        evidence=evidence,
        kernel=kernel,
        tool_context=tool_context,
        start_time=0.0,
        isolated=False,
    )

    patch_content = (
        "*** Begin Patch\n"
        "*** Update File: README.md\n"
        "@@\n"
        "-# hello\n"
        "+# hello world\n"
        "*** End Patch\n"
    )
    tc = ToolCall(id="c1", name="apply_patch", arguments={"patch": patch_content})

    tc_res, content, changed, meta = _execute_tool_call_rich(ctx, tc, turn=1)
    assert tc_res.id == "c1"
    assert changed == ["README.md"]
    assert meta is not None
    assert meta["evidence_kind"] == "patch"
    assert meta["evidence_source"] == "apply_patch"
    assert meta["trace_id"] is not None
