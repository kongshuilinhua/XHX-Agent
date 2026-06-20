"""safety/permissions/rules.py 单测：规则解析、内容提取、规则引擎。"""

from __future__ import annotations

from pathlib import Path

import pytest

from xhx_agent.safety.permissions.rules import (
    Rule,
    RuleEngine,
    _load_rules_file,
    extract_content,
    parse_rule,
)


def test_parse_rule_ok_and_invalid() -> None:
    r = parse_rule("Bash(rm *)", "deny")
    assert r.tool_name == "Bash" and r.pattern == "rm *" and r.effect == "deny"
    with pytest.raises(ValueError):
        parse_rule("not a rule", "allow")


def test_rule_matches() -> None:
    r = parse_rule("Bash(rm *)", "deny")
    assert r.matches("Bash", "rm -rf x") is True
    assert r.matches("Bash", "ls") is False
    assert r.matches("Read", "rm x") is False
    assert "Bash" in repr(r)


def test_extract_content_fields() -> None:
    assert extract_content("Bash", {"command": "ls -la"}) == "ls -la"
    assert extract_content("read_file", {"path": "a.py"}) == "a.py"
    assert extract_content("web_fetch", {"url": "http://x"}) == "http://x"
    assert extract_content("UnknownTool", {"x": 1}) == ""


def test_extract_content_apply_patch_envelope() -> None:
    patch = "*** Begin Patch\n*** Update File: src/foo.py\n@@\n-old\n+new\n*** End Patch"
    out = extract_content("apply_patch", {"patch": patch})
    assert "src/foo.py" in out
    assert extract_content("apply_patch", {}) == ""


def test_load_rules_file_yaml_and_json(tmp_path: Path) -> None:
    yfile = tmp_path / "permissions.yaml"
    yfile.write_text(
        '- rule: "Bash(rm *)"\n  effect: deny\n- rule: "read_file(**)"\n  effect: allow\n', encoding="utf-8"
    )
    rules = _load_rules_file(yfile)
    assert len(rules) == 2 and rules[0].effect == "deny"

    jfile = tmp_path / "permissions.json"
    jfile.write_text('[{"rule": "Bash(ls *)", "effect": "allow"}]', encoding="utf-8")
    assert _load_rules_file(jfile)[0].effect == "allow"

    # 不存在 / 非列表 / 非法 effect 都返回空或跳过
    assert _load_rules_file(tmp_path / "nope.yaml") == []
    bad = tmp_path / "bad.json"
    bad.write_text('[{"rule": "Bash(x)", "effect": "maybe"}]', encoding="utf-8")
    assert _load_rules_file(bad) == []


def test_rule_engine_last_match_wins(tmp_path: Path) -> None:
    pfile = tmp_path / "permissions.yaml"
    pfile.write_text(
        '- rule: "Bash(*)"\n  effect: allow\n- rule: "Bash(rm *)"\n  effect: deny\n',
        encoding="utf-8",
    )
    eng = RuleEngine(project_rules_path=pfile)
    # 层内 last-match-wins：rm 命中后写的 deny
    assert eng.evaluate("Bash", "rm -rf /") == "deny"
    # 只命中第一条 allow
    assert eng.evaluate("Bash", "ls") == "allow"
    # 无匹配
    assert eng.evaluate("Read", "x") is None


def test_rule_engine_append_local(tmp_path: Path) -> None:
    local = tmp_path / "permissions.local.json"
    eng = RuleEngine(local_rules_path=local)
    eng.append_local_rule(Rule("Bash", "git *", "allow"))
    assert local.is_file()
    assert eng.evaluate("Bash", "git status") == "allow"
    # 再追加一条并确认持久化累加
    eng.append_local_rule(Rule("read_file", "**", "allow"))
    assert len(_load_rules_file(local)) == 2
