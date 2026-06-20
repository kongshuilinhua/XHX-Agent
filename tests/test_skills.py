from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.hooks import hooks_manager
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.runtime.mcp_config import MCPServerConfig
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.skills.mcp import MCPManager
from xhx_agent.skills.metadata import Skill, SkillMetadata
from xhx_agent.tools import ToolRegistry

FAKE_MCP_SERVER = str(Path(__file__).parent / "mcp_fake_server.py")


def test_skill_models() -> None:
    # Test valid parsing
    meta = SkillMetadata(
        name="test-skill", description="A test skill", triggers=["deploy", "build"], permissions={"network": "allow"}
    )
    assert meta.name == "test-skill"
    assert meta.triggers == ["deploy", "build"]
    assert meta.permissions == {"network": "allow"}

    skill = Skill(
        name=meta.name,
        description=meta.description,
        triggers=meta.triggers,
        permissions=meta.permissions,
        content="SKILL.md content",
    )
    assert skill.content == "SKILL.md content"


def test_skill_loader_word_boundary_matching() -> None:
    loader = SkillLoader(Path("/tmp"))

    # Test alphanumeric keyword matching
    assert loader.matches_trigger("deploy", "We need to deploy the app") is True
    # Test substring matching prevention
    assert loader.matches_trigger("hi", "this is a task") is False
    assert loader.matches_trigger("hi", "say hi") is True
    # Test regex patterns
    assert loader.matches_trigger(r"deploy-\w+", "Run deploy-prod now") is True
    assert loader.matches_trigger(r"deploy-\w+", "Run deploy- now") is False


def test_skill_loader_progressive_disclosure(tmp_path: Path) -> None:
    # Create mock skill folders in .xhx/skills/
    skills_dir = tmp_path / ".xhx" / "skills"
    skills_dir.mkdir(parents=True)

    # Skill 1: matched
    skill1_dir = skills_dir / "skill1"
    skill1_dir.mkdir()
    with open(skill1_dir / "SKILL.json", "w", encoding="utf-8") as f:
        json.dump({"name": "skill1", "description": "First skill", "triggers": ["deploy"], "permissions": {}}, f)
    with open(skill1_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write("# Skill 1 Instructions")

    # Skill 2: not matched
    skill2_dir = skills_dir / "skill2"
    skill2_dir.mkdir()
    with open(skill2_dir / "SKILL.json", "w", encoding="utf-8") as f:
        json.dump({"name": "skill2", "description": "Second skill", "triggers": ["cleanup"], "permissions": {}}, f)
    with open(skill2_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write("# Skill 2 Instructions")

    loader = SkillLoader(tmp_path)

    # 1. Test lazy loading (available skills shouldn't load content)
    available = loader.load_available_skills()
    assert len(available) == 2
    for s in available:
        assert s.content is None

    # 2. Test trigger matching & lazy loading contents
    matched = loader.match_skills("We want to deploy to staging")
    assert len(matched) == 1
    assert matched[0].name == "skill1"
    assert matched[0].content == "# Skill 1 Instructions"


def test_skill_loader_logging_warnings(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    from xhx_agent.skills.loader import SkillLoader

    # 1. Create a skill directory with corrupted SKILL.md containing invalid YAML
    skills_dir = tmp_path / ".xhx" / "skills" / "broken_skill"
    skills_dir.mkdir(parents=True)

    md_file = skills_dir / "SKILL.md"
    md_file.write_text("---\nname: broken\ntriggers:\n  - [unclosed bracket\n---\nSkill body\n", encoding="utf-8")

    # 2. Trigger skill loader and capture standard warnings
    loader = SkillLoader(tmp_path)
    with caplog.at_level(logging.WARNING):
        loader.load_available_skills()

    assert any("Failed to parse YAML frontmatter" in record.message for record in caplog.records)


def test_hooks_registration_and_execution_order() -> None:
    calls = []

    def cb1(*args, **kwargs):
        calls.append("before_plan_1")

    def cb2(*args, **kwargs):
        calls.append("before_plan_2")

    def cb3(*args, **kwargs):
        calls.append("after_verify")

    hooks_manager.clear()
    hooks_manager.register("before_plan", cb1)
    hooks_manager.register("before_plan", cb2)
    hooks_manager.register("after_verify", cb3)

    # Trigger before_plan
    hooks_manager.trigger("before_plan")
    assert calls == ["before_plan_1", "before_plan_2"]

    # Trigger after_verify
    hooks_manager.trigger("after_verify")
    assert calls == ["before_plan_1", "before_plan_2", "after_verify"]

    # Trigger with invalid stage
    with pytest.raises(ValueError):
        hooks_manager.register("invalid_stage", cb1)


def test_mcp_manager_stdio_connect_list_call() -> None:
    # 用真实 stdio MCP server（FastMCP 子进程）跑通 connect → list_tools → call_tool 全链路。
    cfg = MCPServerConfig(name="fake", command=sys.executable, args=[FAKE_MCP_SERVER])
    mgr = MCPManager()
    try:
        mgr.connect_all([cfg])
        tool_names = {t.name for t in mgr.list_tools("fake")}
        assert {"echo", "add"} <= tool_names

        result = mgr.call_tool("fake", "echo", {"text": "hi"})
        texts = [getattr(c, "text", None) for c in result.content]
        assert "echo: hi" in texts
    finally:
        mgr.close()


def test_mcp_manager_register_and_close_unregisters() -> None:
    import asyncio

    cfg = MCPServerConfig(name="demo", command=sys.executable, args=[FAKE_MCP_SERVER])
    mgr = MCPManager()
    registry = ToolRegistry()
    try:
        mgr.connect_all([cfg])
        mgr.register_tools_to_registry(registry)

        # 命名空间 + schema：模型能看见
        tool_names = {t.name for t in registry.list_tools()}
        assert "mcp_demo_echo" in tool_names
        tool = registry.get("mcp_demo_echo")
        assert tool is not None and tool.name == "mcp_demo_echo"
        assert any(s["function"]["name"] == "mcp_demo_echo" for s in registry.get_all_schemas())

        # 经 Tool 实例直接执行
        from pydantic import BaseModel as PydanticBaseModel

        class _TestParams(PydanticBaseModel):
            text: str = "yo"

        result = asyncio.run(tool.execute(_TestParams(text="yo")))
        assert result is not None
        assert not result.is_error
        assert "echo: yo" in result.output
    finally:
        mgr.close()

    # close 后注销：共享 registry 不残留陈旧定义
    tool_names_after = {t.name for t in registry.list_tools()}
    assert "mcp_demo_echo" not in tool_names_after


def test_mcp_manager_connect_failure_isolated() -> None:
    # 不存在的 command → 连接失败，只回调 on_error 并跳过，不抛。
    bad = MCPServerConfig(name="bad", command="this_command_does_not_exist_xhx_test", args=[])
    mgr = MCPManager()
    errors: list[str] = []
    try:
        mgr.connect_all([bad], on_error=lambda name, _e: errors.append(name))
        assert errors == ["bad"]
        assert "bad" not in mgr._sessions
    finally:
        mgr.close()


def test_skills_compiler_integration(tmp_path: Path) -> None:
    # Write a skill directory under tmp_path
    skills_dir = tmp_path / ".xhx" / "skills"
    skills_dir.mkdir(parents=True)
    skill_dir = skills_dir / "my_custom_skill"
    skill_dir.mkdir()
    with open(skill_dir / "SKILL.json", "w", encoding="utf-8") as f:
        json.dump(
            {
                "name": "my_custom_skill",
                "description": "Custom developer actions",
                "triggers": ["custom-action"],
                "permissions": {},
            },
            f,
        )
    with open(skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write("# Execute custom action steps here")

    scan = ProjectScan(root=str(tmp_path), detected_languages=[], python={}, node={}, file_count=0)

    # Compile context with matching task
    pack = compile_context_pack(workspace=tmp_path, task="Please run the custom-action on the code", scan=scan)

    # Verify skill is compiled into ContextPack items
    skill_items = [item for item in pack.items if item.kind == "skill"]
    assert len(skill_items) == 1
    assert skill_items[0].source == "my_custom_skill"
    assert "custom action steps" in skill_items[0].content


def test_runtime_app_hooks_integration(tmp_path: Path) -> None:
    """Verify hooks trigger/register work correctly with the new HookManagerCompat."""
    hooks_called = []
    hooks_manager.clear()
    hooks_manager.register("before_plan", lambda *a, **k: hooks_called.append("before_plan"))
    hooks_manager.register("after_verify", lambda *a, **k: hooks_called.append("after_verify"))
    hooks_manager.register("before_summary", lambda *a, **k: hooks_called.append("before_summary"))

    # Trigger hooks directly — old integration path (run_task → _run_linear → hooks)
    # has been replaced by orchestrator-based execution. The hooks manager still
    # works for user-registered callbacks and new HookEngine events.
    hooks_manager.trigger("before_plan")
    hooks_manager.trigger("before_summary")

    assert "before_plan" in hooks_called
    assert "before_summary" in hooks_called
    assert "after_verify" not in hooks_called  # not triggered


def test_mcp_resolve_headers_static_token() -> None:
    mgr = MCPManager()
    cfg = MCPServerConfig(name="r", transport="http", url="https://x/mcp", auth_token="tok")
    headers = mgr._resolve_headers(cfg)
    assert headers["Authorization"] == "Bearer tok"


def test_mcp_resolve_headers_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_MCP_TOKEN", "envtok")
    mgr = MCPManager()
    cfg = MCPServerConfig(name="r", transport="http", url="https://x/mcp", auth_token_env="MY_MCP_TOKEN")
    headers = mgr._resolve_headers(cfg)
    assert headers["Authorization"] == "Bearer envtok"


def test_mcp_resolve_headers_no_token() -> None:
    mgr = MCPManager()
    cfg = MCPServerConfig(name="r", transport="http", url="https://x/mcp")
    assert "Authorization" not in mgr._resolve_headers(cfg)


def test_mcp_build_transport_selects_client(monkeypatch: pytest.MonkeyPatch) -> None:
    import xhx_agent.skills.mcp as mcpmod

    calls: dict[str, Any] = {}

    def fake_http(url: str, headers: Any = None, **_kw: Any) -> str:
        calls["http"] = (url, headers)
        return "HTTP_CM"

    def fake_sse(url: str, headers: Any = None, **_kw: Any) -> str:
        calls["sse"] = (url, headers)
        return "SSE_CM"

    monkeypatch.setattr(mcpmod, "streamablehttp_client", fake_http)
    monkeypatch.setattr(mcpmod, "sse_client", fake_sse)

    mgr = MCPManager()
    http_cm = mgr._build_transport(MCPServerConfig(name="r", transport="http", url="https://h/mcp", auth_token="t"))
    assert http_cm == "HTTP_CM"
    assert calls["http"][0] == "https://h/mcp"
    assert calls["http"][1]["Authorization"] == "Bearer t"

    sse_cm = mgr._build_transport(MCPServerConfig(name="r2", transport="sse", url="https://s/sse"))
    assert sse_cm == "SSE_CM"
    assert calls["sse"][0] == "https://s/sse"
