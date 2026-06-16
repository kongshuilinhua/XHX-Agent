"""Agent 定义解析器：Markdown + YAML frontmatter → AgentDef。

来源：mewcode agents/parser.py，适配 XHX-Agent。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

VALID_MODELS = {"inherit", "sonnet", "opus", "haiku", ""}
VALID_PERMISSION_MODES = {"default", "acceptEdits", "dontAsk", ""}
VALID_ISOLATION_MODES = {"", "worktree"}


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class AgentParseError(Exception):
    """Agent 定义文件解析错误。"""
    pass


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class AgentDef:
    """Agent 定义：从 .md 文件的 YAML frontmatter 解析而来。

    Example frontmatter::

        ---
        name: Explore
        description: 快速只读搜索代码的子 Agent
        tools: [Glob, Grep, ReadFile]
        model: haiku
        maxTurns: 30
        ---

        (Markdown 正文 = system prompt)
    """
    agent_type: str
    when_to_use: str
    system_prompt: str = ""
    tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    model: str = "inherit"
    max_turns: int = 50
    permission_mode: str = "default"
    background: bool = False
    isolation: str = ""           # "" | "worktree"
    file_path: Path | None = None
    source: str = "builtin"       # "builtin" | "user" | "project"


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + Markdown body。委托给共享工具。"""
    from xhx_agent.utils.frontmatter import FrontmatterParseError, parse_frontmatter as _parse

    try:
        return _parse(raw)
    except FrontmatterParseError as e:
        raise AgentParseError(str(e)) from e


def _validate_agent_meta(meta: dict, source: str = "") -> None:
    """验证 Agent 元数据字段。"""
    ctx = f" in {source}" if source else ""

    if "name" not in meta:
        raise AgentParseError(f"Missing required field 'name'{ctx}")
    if "description" not in meta:
        raise AgentParseError(f"Missing required field 'description'{ctx}")

    model = str(meta.get("model", "inherit"))
    if model not in VALID_MODELS:
        raise AgentParseError(
            f"Invalid model '{model}'{ctx}: must be one of {VALID_MODELS - {''}}"
        )

    pm = str(meta.get("permissionMode", "default"))
    if pm not in VALID_PERMISSION_MODES:
        raise AgentParseError(
            f"Invalid permissionMode '{pm}'{ctx}: "
            f"must be one of {VALID_PERMISSION_MODES - {''}}"
        )

    max_turns = meta.get("maxTurns")
    if max_turns is not None:
        if not isinstance(max_turns, int) or max_turns <= 0:
            raise AgentParseError(
                f"Invalid maxTurns '{max_turns}'{ctx}: must be a positive integer"
            )

    isolation = str(meta.get("isolation", ""))
    if isolation not in VALID_ISOLATION_MODES:
        raise AgentParseError(
            f"Invalid isolation '{isolation}'{ctx}: "
            f"must be one of {VALID_ISOLATION_MODES - {''}}"
        )


def parse_agent_file(path: Path) -> AgentDef:
    """从 .md 文件解析 AgentDef。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise AgentParseError(f"Cannot read agent file {path}: {e}") from e

    meta, body = parse_frontmatter(raw)
    _validate_agent_meta(meta, str(path))

    return AgentDef(
        agent_type=meta["name"],
        when_to_use=meta["description"],
        system_prompt=body,
        tools=meta.get("tools", []),
        disallowed_tools=meta.get("disallowedTools", []),
        model=str(meta.get("model", "inherit")),
        max_turns=meta.get("maxTurns", 50),
        permission_mode=str(meta.get("permissionMode", "default")),
        background=bool(meta.get("background", False)),
        isolation=str(meta.get("isolation", "")),
        file_path=path,
        source="builtin",
    )
