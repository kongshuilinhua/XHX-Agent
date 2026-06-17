from xhx_agent.hooks import HooksManager, hooks_manager  # noqa: F401  # re-export
from xhx_agent.skills.directory import register_skill_tools
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.skills.mcp import MCPManager
from xhx_agent.skills.metadata import Skill, SkillMetadata
from xhx_agent.skills.parser import SkillDef, SkillParseError, parse_skill_file

__all__ = [
    "MCPManager",
    "Skill",
    "SkillDef",
    "SkillLoader",
    "SkillMetadata",
    "SkillParseError",
    "HooksManager",
    "hooks_manager",
    "parse_skill_file",
    "register_skill_tools",
]
