import asyncio
from pathlib import Path

import pytest
from pydantic import BaseModel

from xhx_agent.commands import CommandContext, CommandRegistry
from xhx_agent.commands.handlers.skill import handle_skill
from xhx_agent.commands.handlers.skill_register import register_skill_commands
from xhx_agent.conversation import ConversationManager
from xhx_agent.skills.directory import register_skill_tools
from xhx_agent.skills.executor import SkillDependencyError, SkillExecutor, filter_tool_registry
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.skills.parser import SkillDef
from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import Tool, ToolResult
from xhx_agent.tools.load_skill import LoadSkill, LoadSkillParams


def test_conversation_update_environment():
    conv = ConversationManager()
    assert not conv.env_injected
    conv.inject_environment("env1")
    assert conv.env_injected
    assert conv.history[0].content == "env1"

    conv.update_environment("env2")
    assert conv.env_injected
    assert conv.history[0].content == "env2"


class MockAgent:
    def __init__(self):
        self.active_skills = {}
        self.work_dir = "."
        self.max_iterations = 5
        self.context_window = 10000
        self.registry = ToolRegistry()
        self._current_conversation = None

    def activate_skill(self, name: str, prompt_body: str):
        self.active_skills[name] = prompt_body
        if self._current_conversation:
            self._current_conversation.update_environment(prompt_body)


def test_executor_execute_inline():
    agent = MockAgent()
    executor = SkillExecutor(agent=agent)
    skill = SkillDef(
        name="test-inline",
        description="test inline desc",
        prompt_body="hello $ARGUMENTS",
    )
    executor.execute_inline(skill, "world")
    assert agent.active_skills["test-inline"] == "hello world"


def test_filter_tool_registry():
    registry = ToolRegistry()

    class DummyParams(BaseModel):
        pass

    class DummyTool(Tool):
        name = "dummy"
        description = "dummy desc"
        params_model = DummyParams

        async def execute(self, params: BaseModel) -> ToolResult:
            return ToolResult(output="dummy")

    dummy = DummyTool()
    registry.register(dummy)

    # 1. empty allowed -> returns registry
    res = filter_tool_registry(registry, [])
    assert res is registry

    # 2. allowed with dummy -> returns new registry with dummy
    res2 = filter_tool_registry(registry, ["dummy"])
    assert res2.get("dummy") is dummy

    # 3. allowed with nonexistent -> raises SkillDependencyError
    with pytest.raises(SkillDependencyError):
        filter_tool_registry(registry, ["nonexistent"])


def test_directory_register_skill_tools(tmp_path: Path):
    skill_dir = tmp_path / "my_skill"
    skill_dir.mkdir()
    tool_json = skill_dir / "tool.json"
    tool_json.write_text(
        '{"name": "custom_tool", "description": "desc", "parameters": {"type": "object", "properties": {}}}',
        encoding="utf-8",
    )
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    tool_py = ref_dir / "custom_tool.py"
    tool_py.write_text('def execute(**kwargs):\n    return "success"\n', encoding="utf-8")

    registry = ToolRegistry()
    count = register_skill_tools(skill_dir, registry)
    assert count == 1
    tool = registry.get("custom_tool")
    assert tool is not None
    assert tool.name == "custom_tool"
    assert tool.description == "desc"

    # Test executing the custom tool
    res = asyncio.run(tool.execute(tool.params_model()))
    assert res.output == "success"
    assert not res.is_error


def test_load_skill_tool(tmp_path: Path):
    loader = SkillLoader(tmp_path)
    # create a mock builtin skill
    skill_def = SkillDef(name="test_skill", description="desc", prompt_body="sop body")
    loader.load_all = lambda: {"test_skill": skill_def}
    loader._cache["test_skill"] = (skill_def, 0.0)

    agent = MockAgent()
    tool = LoadSkill(loader=loader)
    tool.set_agent(agent)

    # Execute LoadSkill
    params = LoadSkillParams(name="test_skill")
    res = asyncio.run(tool.execute(params))
    assert "activated" in res.output
    assert agent.active_skills["test_skill"] == "sop body"


class MockUI:
    def __init__(self):
        self.messages = []

    def add_system_message(self, text: str):
        self.messages.append(text)


def test_skill_command_handlers(tmp_path: Path):
    loader = SkillLoader(tmp_path)
    skill_def = SkillDef(name="my_skill", description="my desc", prompt_body="body")
    loader.load_all = lambda: {"my_skill": skill_def}
    loader._cache["my_skill"] = (skill_def, 0.0)

    ui = MockUI()
    ctx = CommandContext(args="list", ui=ui, config={"skill_loader": loader})

    # Test /skill list
    asyncio.run(handle_skill(ctx))
    assert any("my_skill" in msg for msg in ui.messages)

    # Test /skill info my_skill
    ui.messages.clear()
    ctx.args = "info my_skill"
    asyncio.run(handle_skill(ctx))
    assert any("my_skill" in msg for msg in ui.messages)
    assert any("my desc" in msg for msg in ui.messages)

    # Test /skill reload
    ui.messages.clear()
    ctx.args = "reload"
    asyncio.run(handle_skill(ctx))
    assert any("重新加载" in msg for msg in ui.messages)


def test_register_skill_commands(tmp_path: Path):
    loader = SkillLoader(tmp_path)
    skill_def = SkillDef(name="my_skill", description="my desc", prompt_body="body")
    loader.load_all = lambda: {"my_skill": skill_def}
    loader._cache["my_skill"] = (skill_def, 0.0)

    registry = CommandRegistry()
    register_skill_commands(registry, loader)
    assert registry.find("my_skill") is not None
