from xhx_agent.tools.registry import default_tool_registry


def test_tool_schemas_lists_core_tools():
    schemas = default_tool_registry().tool_schemas()
    names = {s["function"]["name"] for s in schemas}
    assert {"search", "read_file", "apply_patch"} <= names


def test_schema_shape_openai_function():
    schemas = default_tool_registry().tool_schemas()
    s = next(s for s in schemas if s["function"]["name"] == "read_file")
    assert s["type"] == "function"
    assert s["function"]["parameters"]["required"] == ["path"]


def test_terminal_and_verify_in_schemas():
    from xhx_agent.tools.registry import default_tool_registry

    names = {s["function"]["name"] for s in default_tool_registry().tool_schemas()}
    assert {"terminal", "verify"} <= names


def test_command_tools_flagged():
    from xhx_agent.tools.registry import default_tool_registry

    reg = default_tool_registry()
    assert reg.definition("terminal").is_command is True
    assert reg.definition("verify").is_command is True
    assert reg.definition("read_file").is_command is False


def test_command_tools_not_in_tools_map():
    from xhx_agent.tools.registry import default_tool_registry

    reg = default_tool_registry()
    # command tools have no runner → not registered in the structured runner map
    assert "terminal" not in reg.names
    assert "verify" not in reg.names
    assert "read_file" in reg.names
