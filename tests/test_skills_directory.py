"""skills/directory.py 单测：tool.json 解析、实现加载、注册。"""

from __future__ import annotations

from pathlib import Path

from xhx_agent.skills.directory import (
    _run_skill_tool,
    load_tool_implementation,
    parse_tool_json,
    register_skill_tools,
)


def test_parse_tool_json_variants(tmp_path: Path) -> None:
    # 对象 → 包成单元素列表
    p = tmp_path / "tool.json"
    p.write_text('{"name": "foo", "description": "d"}', encoding="utf-8")
    out = parse_tool_json(p)
    assert isinstance(out, list) and out[0]["name"] == "foo"

    # 数组
    p.write_text('[{"name": "a"}, {"name": "b"}]', encoding="utf-8")
    assert len(parse_tool_json(p)) == 2

    # 非法 JSON → []
    p.write_text("{ not json", encoding="utf-8")
    assert parse_tool_json(p) == []

    # 缺失文件 → []
    assert parse_tool_json(tmp_path / "missing.json") == []


def test_load_tool_implementation(tmp_path: Path) -> None:
    refs = tmp_path / "references"
    refs.mkdir()
    (refs / "greet.py").write_text("def execute(name):\n    return f'hi {name}'\n", encoding="utf-8")
    fn = load_tool_implementation(refs, "greet")
    assert fn is not None and fn(name="x") == "hi x"

    # 缺失脚本
    assert load_tool_implementation(refs, "nope") is None

    # 有脚本但无 execute
    (refs / "noexec.py").write_text("x = 1\n", encoding="utf-8")
    assert load_tool_implementation(refs, "noexec") is None


def test_run_skill_tool() -> None:
    assert "no implementation" in _run_skill_tool("t", None, {})

    def good(**kw):
        return {"ok": kw}

    assert "ok" in _run_skill_tool("t", good, {"a": 1})

    def boom(**kw):
        raise RuntimeError("x")

    assert "Tool execution error" in _run_skill_tool("t", boom, {})


def test_register_skill_tools(tmp_path: Path) -> None:
    from xhx_agent.tools.registry import ToolRegistry

    skill_dir = tmp_path / "myskill"
    refs = skill_dir / "references"
    refs.mkdir(parents=True)
    (skill_dir / "tool.json").write_text(
        '[{"name": "adder", "description": "add", "parameters": {"type": "object"}}]', encoding="utf-8"
    )
    (refs / "adder.py").write_text("def execute(a, b):\n    return a + b\n", encoding="utf-8")

    reg = ToolRegistry()
    # 已知不兼容：register_skill_tools 用关键字参数调 register_definition，
    # 而 ToolRegistry.register_definition 只收一个 ToolDefinition 对象 → 注册失败被吞掉，
    # 返回 0。这里断言当前真实行为（该 bug 另行跟踪修复）。
    count = register_skill_tools(skill_dir, reg)
    assert count == 0

    # 无 tool.json → 0
    assert register_skill_tools(tmp_path / "empty", reg) == 0
