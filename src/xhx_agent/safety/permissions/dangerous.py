"""危险命令检测器：正则黑名单 + 安全命令白名单。

与 safety/risk.py（shlex 分词风险分级）互补：
- risk.classify_command → 命令结构级安全判定（首 token 黑名单、解释器内联执行等）
- 本模块 → 正则模式匹配（管道执行远程脚本、写磁盘设备等纵深防御层）

注意：与 risk.py 存在部分重叠（管道字符、rm 等），这是刻意为之的纵深防御——
risk.py 的 shlex 分词是第一道防线，本模块的正则是第二道。两道防线独立故障不会同时失效。
PermissionChecker 在 Layer 1 和 Layer 1b 中同时使用两者。
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 危险模式（正则 + 原因）
# ---------------------------------------------------------------------------

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"rm\s+-[a-z]*r[a-z]*f[a-z]*\s+/\s*$"), "递归强制删除根目录"),
    (re.compile(r"mkfs\."), "格式化磁盘"),
    (re.compile(r"dd\s+if=.*of=/dev/"), "直接写磁盘设备"),
    (re.compile(r"chmod\s+-R\s+777\s+/"), "递归修改根目录权限"),
    (re.compile(r":\(\)\{\s*:\|:&\s*\};:"), "fork bomb"),
    (re.compile(r"curl\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r"wget\s+.*\|\s*(ba)?sh"), "管道执行远程脚本"),
    (re.compile(r">\s*/dev/sd"), "覆盖磁盘设备"),
]

# ---------------------------------------------------------------------------
# 安全命令白名单（不含管道/重定向/分号/$()/反引号）
# ---------------------------------------------------------------------------

_SAFE_COMMANDS = frozenset(
    {
        "ls",
        "dir",
        "pwd",
        "echo",
        "cat",
        "head",
        "tail",
        "wc",
        "find",
        "which",
        "whereis",
        "whoami",
        "hostname",
        "uname",
        "date",
        "cal",
        "uptime",
        "df",
        "du",
        "free",
        "env",
        "printenv",
        "file",
        "stat",
        "readlink",
        "realpath",
        "basename",
        "dirname",
        "sort",
        "uniq",
        "tr",
        "cut",
        "awk",
        "sed",
        "grep",
        "egrep",
        "fgrep",
        "diff",
        "comm",
        "tee",
        "xargs",
        "true",
        "false",
        "test",
        "git status",
        "git log",
        "git diff",
        "git show",
        "git branch",
        "git tag",
        "git remote",
        "git rev-parse",
        "git ls-files",
        "git blame",
        "git stash list",
        "go version",
        "go env",
        "node -v",
        "npm -v",
        "npx",
        "python --version",
        "pip list",
        "cargo --version",
        "rustc --version",
        "java -version",
        "java --version",
    }
)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def is_safe_command(command: str) -> bool:
    """快速判定命令是否在安全白名单内。

    包含管道符、分号、重定向、命令替换的命令一律不视为安全。
    """
    trimmed = command.strip()
    if not trimmed:
        return False
    for ch in ("|", ";", "&&", ">", "$(", "`"):
        if ch in trimmed:
            return False
    return any(trimmed == safe or trimmed.startswith(safe + " ") for safe in _SAFE_COMMANDS)


class DangerousCommandDetector:
    """正则黑名单检测器，支持外部注入额外模式。"""

    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        if extra_patterns:
            for regex_str, reason in extra_patterns:
                self._patterns.append((re.compile(regex_str), reason))

    def detect(self, command: str) -> tuple[bool, str]:
        """返回 ``(is_dangerous, reason)``。"""
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""
