"""权限规则引擎：基于 YAML/JSON 规则文件的工具级访问控制。

来源：mewcode permissions/rules.py，适配 XHX-Agent（同时支持 YAML 和 JSON 格式）。

规则格式示例：
    YAML:
      - rule: "ReadFile(foo/bar/**)"
        effect: allow
      - rule: "Bash(rm *)"
        effect: deny

    JSON:
      [{"rule": "ReadFile(foo/bar/**)", "effect": "allow"}]

三层加载顺序（优先级递增）：
    1. user    (~/.xhx/permissions.yaml/json)
    2. project (.xhx/permissions.yaml/json)
    3. local   (.xhx/permissions.local.yaml/json)   ← 最高优先级
每层内部 last-match-wins。
"""

from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal

import yaml

Effect = Literal["allow", "deny"]

# ---------------------------------------------------------------------------
# 工具 → 内容字段映射
# ---------------------------------------------------------------------------

_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")

_CONTENT_FIELDS: dict[str, str] = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
    # XHX-Agent 工具名
    "read_file": "path",
    "apply_patch": "patch",
    "search": "glob",
    "terminal": "command",
    "web_fetch": "url",
    "web_search": "query",
}

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


class Rule:
    """单条权限规则：工具名 + fnmatch 模式 → allow/deny。"""

    __slots__ = ("tool_name", "pattern", "effect")

    def __init__(self, tool_name: str, pattern: str, effect: Effect) -> None:
        self.tool_name = tool_name
        self.pattern = pattern
        self.effect: Effect = effect

    def matches(self, tool_name: str, content: str) -> bool:
        if self.tool_name != tool_name:
            return False
        return fnmatch(content, self.pattern)

    def __repr__(self) -> str:
        return f"Rule({self.tool_name}({self.pattern}) → {self.effect})"


# ---------------------------------------------------------------------------
# 公开辅助
# ---------------------------------------------------------------------------


def parse_rule(raw: str, effect: Effect) -> Rule:
    """从 ``"ToolName(pattern)"`` 字符串解析规则。"""
    m = _RULE_RE.match(raw.strip())
    if not m:
        raise ValueError(f"无效的规则语法: {raw}")
    return Rule(tool_name=m.group(1), pattern=m.group(2), effect=effect)


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    """从工具参数中提取用于规则匹配的内容字段。

    对 apply_patch，解析 patch 内容提取文件路径供 PathSandbox 检查。
    """
    if tool_name == "apply_patch":
        patch_arg = arguments.get("patch", "")
        if patch_arg:
            try:
                from xhx_agent.tools.patch import _parse_patch
                paths = [op.path for op in _parse_patch(str(patch_arg))]
                return " ".join(paths) if paths else str(patch_arg)
            except Exception:
                return str(patch_arg)
        return ""

    field = _CONTENT_FIELDS.get(tool_name)
    if field is None:
        return ""
    return str(arguments.get(field, ""))


# ---------------------------------------------------------------------------
# 文件加载
# ---------------------------------------------------------------------------


def _load_rules_file(path: Path) -> list[Rule]:
    """从 YAML 或 JSON 文件加载规则列表。按扩展名自动检测格式。"""
    if not path.is_file():
        return []

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []

    # 自动检测格式
    suffix = path.suffix.lower()
    try:
        if suffix in (".yaml", ".yml"):
            raw = yaml.safe_load(text)
        elif suffix == ".json":
            raw = json.loads(text)
        else:
            # 未知扩展名：先试 YAML，再试 JSON
            try:
                raw = yaml.safe_load(text)
            except yaml.YAMLError:
                raw = json.loads(text)
    except (yaml.YAMLError, json.JSONDecodeError):
        return []

    if not isinstance(raw, list):
        return []

    rules: list[Rule] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        rule_str = entry.get("rule", "")
        effect = entry.get("effect", "")
        if effect not in ("allow", "deny"):
            continue
        try:
            rules.append(parse_rule(str(rule_str), effect))
        except ValueError:
            continue
    return rules


def _find_rules_file(base_dir: Path, stem: str) -> Path | None:
    """在 *base_dir* 下查找 *stem*.yaml / *stem*.yml / *stem*.json。"""
    for ext in (".yaml", ".yml", ".json"):
        candidate = base_dir / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


# ---------------------------------------------------------------------------
# 规则引擎
# ---------------------------------------------------------------------------


class RuleEngine:
    """三层权限规则引擎。

    三层：user → project → local（优先级递增）
    每层内部 last-match-wins。
    """

    def __init__(
        self,
        user_rules_path: Path | None = None,
        project_rules_path: Path | None = None,
        local_rules_path: Path | None = None,
    ) -> None:
        self._user_path = user_rules_path
        self._project_path = project_rules_path
        self._local_path = local_rules_path

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------

    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        """返回匹配到的最高优先级规则的 effect，无匹配返回 None。"""
        for rules in self._load_tiers():
            # 层内 last-match-wins：倒序遍历
            for rule in reversed(rules):
                if rule.matches(tool_name, content):
                    return rule.effect
        return None

    def append_local_rule(self, rule: Rule) -> None:
        """追加一条规则到 local 层文件。"""
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)
        existing.append(rule)
        entries = [
            {"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect}
            for r in existing
        ]
        # 按原扩展名写入
        suffix = self._local_path.suffix.lower()
        if suffix in (".yaml", ".yml"):
            self._local_path.write_text(yaml.dump(entries, allow_unicode=True), encoding="utf-8")
        else:
            self._local_path.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _load_tiers(self) -> list[list[Rule]]:
        tiers: list[list[Rule]] = []
        for p in (self._user_path, self._project_path, self._local_path):
            tiers.append(_load_rules_file(p) if p else [])
        return tiers
