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


def test_chat_and_count_emits_real_token_usage():
    from types import SimpleNamespace

    from xhx_agent.models.types import ChatResult, TokenUsage
    from xhx_agent.orchestrators._toolturn import chat_and_count

    events = []
    ctx = SimpleNamespace(metrics_tracker={"tokens": 0}, event_callback=lambda e: events.append(e))

    class FakeClient:
        def chat(self, messages, schemas):
            return ChatResult(content="ok", usage=TokenUsage(prompt=10, completion=6, total=16))

    result = chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "hi"}], [])

    assert result.content == "ok"
    token_events = [e for e in events if e.type == "token_usage"]
    assert len(token_events) == 1
    assert token_events[0].payload["total"] == 16
    assert token_events[0].payload["cumulative_total"] == 16
    assert ctx.metrics_tracker["tokens_real"] == 16

    # 第二次调用应累加 cumulative_total
    chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "again"}], [])
    token_events = [e for e in events if e.type == "token_usage"]
    assert token_events[-1].payload["cumulative_total"] == 32


def test_tool_start_and_result_events_payload(tmp_path):
    from xhx_agent.orchestrators._toolturn import _execute_tool_call_rich
    from xhx_agent.runtime.events import RuntimeEvent

    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    app = RuntimeApp(tmp_path)
    app.init_project()
    evidence = EvidenceStore(tmp_path, "run-1")
    kernel = SafeExecutionKernel(tmp_path, "run-1", evidence, app.tool_registry)
    tool_context = ToolContext(workspace=tmp_path, max_file_bytes=100000)

    events = []
    def callback(evt: RuntimeEvent):
        events.append(evt)

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
        event_callback=callback,
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

    _execute_tool_call_rich(ctx, tc, turn=1)

    # Assert tool_start has arguments
    start_events = [e for e in events if e.type == "tool_start"]
    assert len(start_events) == 1
    assert start_events[0].payload.get("arguments") == {"patch": patch_content}

    # Assert tool_result has real status, summary, and arguments
    result_events = [e for e in events if e.type == "tool_result"]
    assert len(result_events) == 1
    assert result_events[0].payload.get("status") == "success"
    assert "README.md" in result_events[0].payload.get("summary", "")
    assert result_events[0].payload.get("arguments") == {"patch": patch_content}



def test_chat_and_count_no_usage_emits_no_token_event():
    from types import SimpleNamespace

    from xhx_agent.models.types import ChatResult
    from xhx_agent.orchestrators._toolturn import chat_and_count

    events = []
    ctx = SimpleNamespace(metrics_tracker={"tokens": 0}, event_callback=lambda e: events.append(e))

    class FakeClient:
        def chat(self, messages, schemas):
            return ChatResult(content="ok")  # usage None（provider 未返回）

    chat_and_count(ctx, FakeClient(), [{"role": "user", "content": "hi"}], [])

    assert [e for e in events if e.type == "token_usage"] == []
    # 估算路径仍然累加，保证回退可用
    assert ctx.metrics_tracker["tokens"] > 0
