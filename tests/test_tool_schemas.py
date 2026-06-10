from xhx_agent.tools.registry import default_tool_registry


def test_tool_schemas_lists_three_tools():
    schemas = default_tool_registry().tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert names == {"search", "read_file", "apply_patch"}


def test_schema_shape_openai_function():
    schemas = default_tool_registry().tool_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "read_file")
    assert s["type"] == "function"
    assert s["function"]["parameters"]["required"] == ["path"]
