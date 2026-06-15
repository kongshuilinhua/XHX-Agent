from xhx_agent.tui.tool_display import tool_header


def test_tool_header_search_and_query():
    assert tool_header("search", {"query": "hello world"}) == 'search "hello world"'
    assert tool_header("repo_query", {"query": "find active"}) == 'repo_query "find active"'
    # missing query
    assert tool_header("search", {}) == "search"


def test_tool_header_read_file():
    assert tool_header("read_file", {"path": "src/main.py"}) == "read_file src/main.py"
    assert tool_header("read_file", {}) == "read_file"


def test_tool_header_terminal_and_verify():
    assert tool_header("terminal", {"command": "pytest"}) == "$ pytest"
    assert tool_header("terminal", {}) == "$"
    assert tool_header("verify", {"command": "pytest"}) == "verify pytest"
    assert tool_header("verify", {}) == "verify (default)"


def test_tool_header_apply_patch():
    patch_update = """*** Begin Patch
*** Update File: src/calc.py
@@ -1,2 +1,2 @@
"""
    assert tool_header("apply_patch", {"patch": patch_update}) == "apply_patch src/calc.py"

    patch_add = """*** Begin Patch
*** Add File: tests/test_calc.py
@@ -0,0 +1,1 @@
"""
    assert tool_header("apply_patch", {"patch": patch_add}) == "apply_patch tests/test_calc.py"

    patch_git = """--- a/src/main.py
+++ b/src/main.py
@@ -1,1 +1,1 @@
"""
    assert tool_header("apply_patch", {"patch": patch_git}) == "apply_patch src/main.py"

    # fallback when no match
    assert tool_header("apply_patch", {"patch": "some generic text"}) == "apply_patch"
    assert tool_header("apply_patch", {}) == "apply_patch"


def test_tool_header_dispatch():
    assert (
        tool_header("dispatch", {"agent_type": "write", "description": "fixing bugs"}) == "dispatch[write] fixing bugs"
    )
    assert tool_header("dispatch", {"description": "fixing bugs"}) == "dispatch[explore] fixing bugs"
    assert tool_header("dispatch", {}) == "dispatch[explore]"


def test_tool_header_fallback():
    assert tool_header("custom_tool", {"arg1": "val1", "arg2": 2}) == 'custom_tool {"arg1":"val1","arg2":2}'

    # Check truncation to ~80 chars
    long_arg = "a" * 100
    res = tool_header("custom_tool", {"long": long_arg})
    assert len(res) <= 85
    assert "..." in res
