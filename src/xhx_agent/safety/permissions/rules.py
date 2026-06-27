"""权限规则引擎：基于 YAML/JSON 规则文件的工具级访问控制。

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

Effect = Literal["allow", "ask", "deny"]

# ---------------------------------------------------------------------------
# Shell 命令规则匹配（对齐 Claude Code shellRuleMatching：exact / prefix(cmd:*) / wildcard）
# ---------------------------------------------------------------------------

# 这些工具的内容是 shell 命令，规则按「命令前缀」语义匹配，而非文件 glob。
_SHELL_TOOLS = frozenset({"Bash", "terminal"})

# 带子命令的二进制：don't-ask-again 生成前缀规则时取前两个 token（如 `git diff`），
# 否则一次「允许 git」会放行整个 git 全家（含 git push）。
_SUBCOMMAND_BINARIES = frozenset(
    {
        "git",
        "npm",
        "npx",
        "yarn",
        "pnpm",
        "bun",
        "cargo",
        "go",
        "docker",
        "kubectl",
        "pip",
        "pip3",
        "uv",
        "poetry",
        "gh",
        "brew",
        "apt",
        "apt-get",
        "systemctl",
        "make",
        "dotnet",
        "gradle",
        "mvn",
        "terraform",
        "helm",
        "aws",
        "gcloud",
    }
)

# 组合命令分隔符：&& || ; | & —— 拆分后每个子命令都要被 allow 才整体放行（堵 `mkdir x && rm` 注入）。
_SHELL_SPLIT_RE = re.compile(r"&&|\|\||;|\||&")


def split_shell_command(command: str) -> list[str]:
    """把组合命令拆成子命令列表（去空白、去空段）。"""
    return [p.strip() for p in _SHELL_SPLIT_RE.split(command) if p.strip()]


def _match_shell_rule(pattern: str, command: str) -> bool:
    """按 exact / prefix(`cmd:*`) / wildcard(`*`) 语义匹配一条 shell 命令。"""
    pat = pattern.strip()
    cmd = command.strip()
    # 1) 前缀语法 `cmd:*` —— 匹配该前缀本身或以「前缀 + 空格」开头
    if pat.endswith(":*"):
        prefix = pat[:-2].strip()
        return cmd == prefix or cmd.startswith(prefix + " ")
    # 2) 含通配符 `*` → 整串正则匹配（按 * 分段、各段转义、用 .* 连接，避免转义错位）。
    #    尾部单个 ` *` 视为可选，使 `git *` 也匹配裸 `git`（对齐 Claude 前缀语义）。
    if "*" in pat:
        optional_tail = pat.endswith(" *") and pat.count("*") == 1
        core = pat[:-2] if optional_tail else pat
        regex = ".*".join(re.escape(seg) for seg in core.split("*"))
        if optional_tail:
            regex += "( .*)?"
        return re.fullmatch(regex, cmd, re.DOTALL) is not None
    # 3) 精确匹配
    return cmd == pat


def _command_prefix(command: str) -> str:
    """提取命令前缀：首 token；首 token 属带子命令的二进制则取前两 token。"""
    toks = command.strip().split()
    if not toks:
        return ""
    if toks[0] in _SUBCOMMAND_BINARIES and len(toks) >= 2:
        return f"{toks[0]} {toks[1]}"
    return toks[0]


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
        # shell 工具按命令前缀语义匹配；其余（文件路径等）走文件 glob。
        if tool_name in _SHELL_TOOLS:
            return _match_shell_rule(self.pattern, content)
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
    对多文件补丁返回空格分隔的路径列表；解析失败时回退到信封格式正则提取，
    再失败返回空字符串（PathSandbox 不做拦截，由 patch 工具自身校验）。
    """
    if tool_name == "apply_patch":
        patch_arg = arguments.get("patch", "")
        if patch_arg:
            patch_str = str(patch_arg)
            try:
                from xhx_agent.tools.patch import _parse_patch

                paths = [op.path for op in _parse_patch(patch_str)]
                return " ".join(paths) if paths else ""
            except Exception:
                # 回退：尝试从信封格式提取路径（*** Update File: <path>）
                import re

                envelope_paths = re.findall(r"\*{3}\s*Update\s*File:\s*(\S+)", patch_str)
                if envelope_paths:
                    return " ".join(envelope_paths)
                # 回退：尝试 unified diff 格式（--- a/<path> 或 +++ b/<path>）
                diff_paths = re.findall(r"^[+]{3}\s+b/(\S+)", patch_str, re.MULTILINE)
                if diff_paths:
                    return " ".join(diff_paths)
                return ""
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
        if effect not in ("allow", "ask", "deny"):
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
        """返回匹配规则按行为优先级 deny > ask > allow 的裁决，无匹配返回 None。

        对齐 Claude Code：deny 永远压过 allow（无论在哪一层、哪个位置），ask 压过 allow。
        三层（user/project/local）的规则全部参与匹配，行为优先级而非层/顺序决定结果——
        这样一条 deny 不会被某层后写的 allow 意外覆盖。
        """
        effects: set[Effect] = set()
        for rules in self._load_tiers():
            for rule in rules:
                if rule.matches(tool_name, content):
                    effects.add(rule.effect)
        for eff in ("deny", "ask", "allow"):
            if eff in effects:
                return eff  # type: ignore[return-value]
        return None

    def append_local_rule(self, rule: Rule) -> None:
        """追加一条规则到 local 层文件。"""
        if self._local_path is None:
            return
        self._local_path.parent.mkdir(parents=True, exist_ok=True)
        existing = _load_rules_file(self._local_path)
        existing.append(rule)
        entries = [{"rule": f"{r.tool_name}({r.pattern})", "effect": r.effect} for r in existing]
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


# ---------------------------------------------------------------------------
# don't-ask-again 规则构造
# ---------------------------------------------------------------------------


def build_allow_always_rule(tool_name: str, arguments: dict[str, Any]) -> Rule | None:
    """从一次工具调用构造「don't ask again」的 allow 规则。

    shell 工具取命令前缀生成 `prefix:*`（如 `git diff:*`），一次批准覆盖该命令族、而非
    只匹配那条完整命令行；文件等工具用精确内容。无可用内容返回 None。
    """
    content = extract_content(tool_name, arguments)
    if not content:
        return None
    if tool_name in _SHELL_TOOLS:
        prefix = _command_prefix(content)
        if not prefix:
            return None
        return Rule(tool_name=tool_name, pattern=f"{prefix}:*", effect="allow")
    return Rule(tool_name=tool_name, pattern=content, effect="allow")
