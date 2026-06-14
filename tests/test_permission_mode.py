from xhx_agent.safety.permission_mode import (
    PERMISSION_MODES,
    next_permission_mode,
    permission_mode_from_string,
    permission_mode_title,
)


def test_permission_modes_list():
    assert PERMISSION_MODES == ("default", "auto", "bypass")

def test_next_permission_mode():
    assert next_permission_mode("default") == "auto"
    assert next_permission_mode("auto") == "bypass"
    assert next_permission_mode("bypass") == "default"
    assert next_permission_mode("invalid") == "default"

def test_permission_mode_from_string():
    assert permission_mode_from_string("default") == "default"
    assert permission_mode_from_string("auto") == "auto"
    assert permission_mode_from_string("bypass") == "bypass"
    assert permission_mode_from_string("invalid") == "default"
    assert permission_mode_from_string("") == "default"

def test_permission_mode_title():
    assert permission_mode_title("default") == "默认"
    assert permission_mode_title("auto") == "自动"
    assert permission_mode_title("bypass") == "越过"
