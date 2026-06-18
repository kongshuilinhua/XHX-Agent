"""共享 YAML frontmatter 解析器。agents/parser 和 skills/parser 共用。"""

from __future__ import annotations

import yaml


class FrontmatterParseError(Exception):
    """Frontmatter 解析错误。"""

    pass


def parse_frontmatter(raw: str) -> tuple[dict, str]:
    """解析 YAML frontmatter + Markdown body。

    Returns:
        (meta_dict, body_text)

    Raises:
        FrontmatterParseError: 格式错误。
    """
    stripped = raw.lstrip()
    if not stripped.startswith("---"):
        raise FrontmatterParseError("Missing YAML frontmatter (must start with ---)")

    end = stripped.find("---", 3)
    if end == -1:
        raise FrontmatterParseError("Unclosed YAML frontmatter (missing closing ---)")

    yaml_block = stripped[3:end]
    body = stripped[end + 3 :].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise FrontmatterParseError(f"Invalid YAML in frontmatter: {e}") from e

    if not isinstance(meta, dict):
        raise FrontmatterParseError("Frontmatter must be a YAML mapping")

    return meta, body
