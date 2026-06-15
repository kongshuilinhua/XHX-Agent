from xhx_agent.repo_intel.index import write_repo_intel_index
from xhx_agent.tools.registry import ToolContext, ToolStep, default_tool_registry


def test_repo_query_definition_and_registration():
    reg = default_tool_registry()
    d = reg.definition("repo_query")
    assert d is not None
    assert d.read_only is True
    assert d.runner is not None

    schemas = reg.tool_schemas()
    repo_schema = [s for s in schemas if s["function"]["name"] == "repo_query"]
    assert len(repo_schema) == 1
    assert repo_schema[0]["function"]["parameters"]["required"] == ["query"]


def test_repo_query_runner_success(tmp_path):
    # Create a dummy file with a symbol and a reference
    (tmp_path / "foo.py").write_text(
        "def calculate_sum(a, b):\n    return a + b\n\ncalculate_sum(1, 2)\n", encoding="utf-8"
    )

    # Build and write index
    write_repo_intel_index(tmp_path)

    reg = default_tool_registry()
    context = ToolContext(workspace=tmp_path)

    # 1. Query symbol
    step_symbol = ToolStep(tool="repo_query", arguments={"query": "calculate_sum", "kind": "symbol"})
    res_symbol = reg.execute(context, step_symbol)
    assert res_symbol.status == "success"
    assert "foo.py:1  calculate_sum (function)" in res_symbol.trace_payload["content"]

    # 2. Query reference
    step_ref = ToolStep(tool="repo_query", arguments={"query": "calculate_sum", "kind": "reference"})
    res_ref = reg.execute(context, step_ref)
    assert res_ref.status == "success"
    assert "foo.py:4  calculate_sum: calculate_sum(1, 2)" in res_ref.trace_payload["content"]


def test_repo_query_runner_empty_and_no_index(tmp_path):
    reg = default_tool_registry()
    context = ToolContext(workspace=tmp_path)

    # No index exists
    step = ToolStep(tool="repo_query", arguments={"query": "some_func", "kind": "symbol"})
    res = reg.execute(context, step)
    assert res.status == "success"
    # Even if index is missing, load_repo_intel_index builds it, but if workspace is empty:
    # it returns success with empty / no matching symbols found.
    assert "No matching symbols found" in res.trace_payload["content"]


def test_repo_query_runner_error_handling(tmp_path, monkeypatch):
    import xhx_agent.repo_intel.index as indexmod

    def mock_load_error(ws):
        raise RuntimeError("Mock load failure")

    monkeypatch.setattr(indexmod, "load_repo_intel_index", mock_load_error)

    reg = default_tool_registry()
    context = ToolContext(workspace=tmp_path)
    step = ToolStep(tool="repo_query", arguments={"query": "some_func", "kind": "symbol"})
    res = reg.execute(context, step)
    assert res.status == "success"
    assert "Failed to load" in res.trace_payload["content"] or "Mock load failure" in res.trace_payload["content"]
