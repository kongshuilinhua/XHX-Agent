from __future__ import annotations

import json
from pathlib import Path

import pytest

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.skills.hooks import hooks_manager
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.skills.mcp import MCPClient
from xhx_agent.skills.metadata import Skill, SkillMetadata
from xhx_agent.tools.registry import ToolContext, ToolRegistry


def test_skill_models() -> None:
    # Test valid parsing
    meta = SkillMetadata(
        name="test-skill",
        description="A test skill",
        triggers=["deploy", "build"],
        permissions={"network": "allow"}
    )
    assert meta.name == "test-skill"
    assert meta.triggers == ["deploy", "build"]
    assert meta.permissions == {"network": "allow"}

    skill = Skill(
        name=meta.name,
        description=meta.description,
        triggers=meta.triggers,
        permissions=meta.permissions,
        content="SKILL.md content"
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
        json.dump({
            "name": "skill1",
            "description": "First skill",
            "triggers": ["deploy"],
            "permissions": {}
        }, f)
    with open(skill1_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write("# Skill 1 Instructions")

    # Skill 2: not matched
    skill2_dir = skills_dir / "skill2"
    skill2_dir.mkdir()
    with open(skill2_dir / "SKILL.json", "w", encoding="utf-8") as f:
        json.dump({
            "name": "skill2",
            "description": "Second skill",
            "triggers": ["cleanup"],
            "permissions": {}
        }, f)
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


def test_mcp_client_mock_mode() -> None:
    # When no command is specified, MCPClient defaults to mock mode
    client = MCPClient()
    assert client.is_mock is True

    # Check list tools
    tools = client.list_tools()
    assert len(tools) == 2
    assert tools[0]["name"] == "mcp_fetch_weather"
    assert tools[1]["name"] == "mcp_calculate"

    # Check tool execution
    res1 = client.call_tool("mcp_fetch_weather", {"city": "Shanghai"})
    assert "Shanghai" in res1["content"][0]["text"]

    res2 = client.call_tool("mcp_calculate", {"expression": "12 * 12"})
    assert res2["content"][0]["text"] == "144"


def test_mcp_client_dynamic_tool_registration() -> None:
    client = MCPClient()
    registry = ToolRegistry()

    # Check original tools
    assert len(registry.names) == 0

    # Register MCP tools
    client.register_tools_to_registry(registry)
    assert "mcp_fetch_weather" in registry.names
    assert "mcp_calculate" in registry.names

    # Check validation bypass (custom tools should pass validation checks)
    from xhx_agent.models.types import ModelPlan, ToolStep
    plan = ModelPlan(
        summary="MCP step",
        steps=[
            ToolStep(tool="mcp_calculate", arguments={"expression": "10 - 2"})
        ]
    )
    registry.validate_plan(plan)  # Should not raise any errors

    # Check execution
    context = ToolContext(workspace=Path("/tmp"))
    result = registry.execute(context, ToolStep(tool="mcp_calculate", arguments={"expression": "21 + 21"}))
    assert result.status == "success"
    assert result.summary == "42"


def test_skills_compiler_integration(tmp_path: Path) -> None:
    # Write a skill directory under tmp_path
    skills_dir = tmp_path / ".xhx" / "skills"
    skills_dir.mkdir(parents=True)
    skill_dir = skills_dir / "my_custom_skill"
    skill_dir.mkdir()
    with open(skill_dir / "SKILL.json", "w", encoding="utf-8") as f:
        json.dump({
            "name": "my_custom_skill",
            "description": "Custom developer actions",
            "triggers": ["custom-action"],
            "permissions": {}
        }, f)
    with open(skill_dir / "SKILL.md", "w", encoding="utf-8") as f:
        f.write("# Execute custom action steps here")

    scan = ProjectScan(
        root=str(tmp_path),
        detected_languages=[],
        python={},
        node={},
        file_count=0
    )

    # Compile context with matching task
    pack = compile_context_pack(
        workspace=tmp_path,
        task="Please run the custom-action on the code",
        scan=scan
    )

    # Verify skill is compiled into ContextPack items
    skill_items = [item for item in pack.items if item.kind == "skill"]
    assert len(skill_items) == 1
    assert skill_items[0].source == "my_custom_skill"
    assert "custom action steps" in skill_items[0].content


def test_runtime_app_hooks_integration(tmp_path: Path) -> None:
    # Verify lifecycle hook triggers function inside RuntimeApp.run_task flow
    from xhx_agent.repo_intel.index import write_repo_intel_index
    from xhx_agent.repo_intel.scanner import scan_project
    from xhx_agent.repo_intel.xhx_md import write_xhx_md
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.runtime.config import write_default_config
    from xhx_agent.runtime.profiles import write_default_profiles

    # Initialize a mock environment
    (tmp_path / ".xhx").mkdir()
    write_default_config(tmp_path)
    write_default_profiles(tmp_path)
    scan = scan_project(tmp_path)
    write_xhx_md(tmp_path, scan)
    write_repo_intel_index(tmp_path)

    # Register lifecycle hooks to verify executions
    hooks_called = []
    hooks_manager.clear()
    hooks_manager.register("before_plan", lambda *a, **k: hooks_called.append("before_plan"))
    hooks_manager.register("after_verify", lambda *a, **k: hooks_called.append("after_verify"))
    hooks_manager.register("before_summary", lambda *a, **k: hooks_called.append("before_summary"))

    # Instantiate RuntimeApp and call run_task in linear loop mode
    # We use a mock profile to avoid hitting an LLM API
    app = RuntimeApp(workspace=tmp_path)

    # We will trigger the linear loop by using a simple task that won't run verification unless we make modifications,
    # but we can verify before_plan and before_summary.
    app.run_task(task="Research how the app works", profile_name="mock")

    assert "before_plan" in hooks_called
    assert "before_summary" in hooks_called
