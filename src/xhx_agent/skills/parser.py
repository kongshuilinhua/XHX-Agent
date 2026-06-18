"""Skill 定义解析器：Markdown + YAML frontmatter → SkillDef。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")
VALID_MODES = {"inline", "fork"}
VALID_CONTEXTS = {"full", "recent", "none"}


class SkillParseError(Exception):
    """Skill 定义文件解析错误。"""
    pass


@dataclass
class SkillDef:
    """Skill 定义：从 SKILL.md 文件的 YAML frontmatter 解析而来。

    Example frontmatter::

        ---
        name: my-skill
        description: My custom skill
        mode: inline
        context: full
        ---

        (Markdown 正文 = 注入的提示词)
    """
    name: str
    description: str
    prompt_body: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    model: str | None = None
    context: Literal["full", "recent", "none"] = "full"
    source_path: Path | None = None
    is_directory: bool = False
    # 兼容旧 Skill 系统
    triggers: list[str] = field(default_factory=list)
    permissions: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + Markdown body。委托给共享工具。"""
    from xhx_agent.utils.frontmatter import FrontmatterParseError, parse_frontmatter as _parse

    try:
        return _parse(raw)
    except FrontmatterParseError as e:
        raise SkillParseError(str(e)) from e


def _validate_meta(meta: dict, source: str = "") -> None:
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise SkillParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise SkillParseError(f"Missing required field 'description'{ctx}")

    name = meta["name"]
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(
            f"Invalid skill name '{name}'{ctx}: "
            "must be lowercase letters, digits, and hyphens, starting with a letter"
        )

    mode = meta.get("mode", "inline")
    if mode not in VALID_MODES:
        raise SkillParseError(f"Invalid mode '{mode}'{ctx}: must be one of {VALID_MODES}")

    context = meta.get("context", "full")
    if context not in VALID_CONTEXTS:
        raise SkillParseError(f"Invalid context '{context}'{ctx}: must be one of {VALID_CONTEXTS}")


def parse_skill_file(path: Path) -> SkillDef:
    """从 SKILL.md 文件解析 SkillDef。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SkillParseError(f"Cannot read skill file {path}: {e}") from e

    meta, body = parse_frontmatter(raw)
    _validate_meta(meta, str(path))

    return SkillDef(
        name=meta["name"],
        description=meta["description"],
        prompt_body=body,
        allowed_tools=meta.get("allowedTools", []),
        mode=meta.get("mode", "inline"),
        model=meta.get("model"),
        context=meta.get("context", "full"),
        source_path=path,
        is_directory=path.parent.is_dir() if path.parent else False,
        triggers=meta.get("triggers", []),
        permissions=meta.get("permissions", {}),
    )


def substitute_arguments(prompt_body: str, args: str) -> str:
    """替换 skill 提示词中的 $ARGUMENTS 占位符。"""
    return prompt_body.replace("$ARGUMENTS", args)
