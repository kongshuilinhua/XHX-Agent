"""命令风险分级器：把 shell 命令判定为 safe / confirm / deny 三档，是 Agent 与宿主机之间的安全边界。

由 safety.policy.decide_terminal 在执行 terminal 命令前调用。关键设计：先 shlex 分词再判定
（防 rm -fr、rm.exe 等绕过），shell 元字符与解释器内联执行（python -c）一律 deny，默认档为 confirm。
改任何判定都要同步更新 tests/test_safety.py 的绕过用例。
"""

from __future__ import annotations

import os
import shlex
from enum import StrEnum
from pathlib import Path


class RiskLevel(StrEnum):
    SAFE = "safe"
    CONFIRM = "confirm"
    DENY = "deny"


SAFE_PREFIXES = ("pwd", "ls", "dir", "rg", "cat", "type", "git status", "git diff")
CONFIRM_PREFIXES = (
    "pytest",
    "python -m pytest",
    "npm test",
    "npm run build",
    "npm run typecheck",
    "uv run pytest",
)

# 这些可执行文件永远不能被判到 DENY 以下：破坏性文件操作、提权、联网下载并执行、
# 以及交互式 shell（后者会让模型把任意命令夹带着绕过本分级器）。
DENY_EXECUTABLES = frozenset(
    {
        # 破坏性 / 改动文件系统
        "rm",
        "rmdir",
        "rd",
        "del",
        "erase",
        "format",
        "mkfs",
        "dd",
        "shred",
        "truncate",
        "mv",
        "move",
        "chmod",
        "chown",
        "chgrp",
        "ln",
        "mklink",
        # 联网下载 / 远程 shell
        "curl",
        "wget",
        "nc",
        "netcat",
        "ncat",
        "telnet",
        "ssh",
        "scp",
        "sftp",
        "ftp",
        # 交互式 shell / shell 外壳
        "sh",
        "bash",
        "zsh",
        "fish",
        "csh",
        "ksh",
        "tcsh",
        "dash",
        "ash",
        "cmd",
        "powershell",
        "pwsh",
        # 提权 / 系统控制
        "sudo",
        "su",
        "doas",
        "runas",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "kill",
        "killall",
        "pkill",
        "taskkill",
        "reg",
        "regedit",
        "diskpart",
        "fdisk",
        "setx",
    }
)

# 这些解释器能通过下列 flag 执行任意内联代码（等同任意代码执行），必须拦截。
_CODE_INTERPRETERS = frozenset({"python", "python3", "py", "node", "nodejs", "deno", "perl", "ruby", "php"})
_INLINE_CODE_FLAGS = frozenset({"-c", "-e", "--eval", "--exec"})

# 纵深防御用的子串模式：即使首 token 分析漏掉（比如本身允许的可执行文件的危险子命令），
# 也能在这里兜住。
DENY_PATTERNS = (
    "rm -rf",
    "git reset --hard",
    "git checkout -- .",
    "git clean -",
    "npm install -g",
    "pip install --global",
    "del /s",
    "rmdir /s",
)

# shell 元字符意味着命令拼接、重定向或替换，绝不放行到自动路径。
# 这里针对「原始」命令（在空白归一化之前）检查，这样换行、分号也无法被夹带进来拆分命令。
_SHELL_METACHARS = (";", "|", "&", "`", "$(", "${", ">", "<", "\n", "\r")


def _executable_name(token: str) -> str:
    """取命令首 token 的可执行名并归一化：去引号、转小写、剥掉 .exe/.cmd/.bat/.com/.ps1 扩展名。

    剥扩展名是关键一步——否则 `rm.exe` 会被当成与黑名单里 `rm` 不同的名字而漏网（Windows 绕过）。
    """
    name = Path(token.strip("'\"")).name.lower()
    if "." in name and name.rsplit(".", 1)[1] in {"exe", "cmd", "bat", "com", "ps1"}:
        name = name.rsplit(".", 1)[0]
    return name


def _is_dangerous_git(tokens: list[str]) -> bool:
    """识别会销毁历史或工作区的 git 子命令（reset --hard/--merge、clean -f/-d、push --force、checkout .）。

    git 本身是允许命令，不能整个拉黑，只能在子命令层面拦截这几类破坏性操作。
    """
    args = [t.lower() for t in tokens[1:]]
    if "reset" in args and ("--hard" in args or "--merge" in args):
        return True
    if "clean" in args and any(t.startswith("-") and ("f" in t or "d" in t) for t in args):
        return True
    if "push" in args and ("--force" in args or "-f" in args):
        return True
    return "checkout" in args and "." in args


def classify_command(command: str) -> RiskLevel:
    """把命令判定为 safe / confirm / deny。判定顺序刻意从严到宽：先拦危险，再放行白名单。"""
    # 1. 任何 shell 元字符（拼接 / 重定向 / 替换）一律直接拒绝。
    if any(char in command for char in _SHELL_METACHARS):
        return RiskLevel.DENY

    normalized = " ".join(command.strip().split()).lower()
    if not normalized:
        return RiskLevel.CONFIRM

    # 2. 用 shlex 分词，使判定与 shell 的真实解析一致，避免子串匹配漏掉 flag 变形（rm -fr、rm -r -f…）。
    try:
        tokens = shlex.split(normalized, posix=(os.name != "nt"))
    except ValueError:
        # 引号不闭合等无法像 shell 那样解析的命令，宁可拒绝也不猜。
        return RiskLevel.DENY
    if not tokens:
        return RiskLevel.CONFIRM

    exe = _executable_name(tokens[0])

    # 3. 命令本身就是黑名单可执行文件。
    if exe in DENY_EXECUTABLES:
        return RiskLevel.DENY

    # 4. 解释器内联执行任意代码。
    if exe in _CODE_INTERPRETERS and any(arg in _INLINE_CODE_FLAGS for arg in (a.lower() for a in tokens[1:])):
        return RiskLevel.DENY

    # 5. 危险的 git 子命令（销毁历史 / 工作区）。
    if exe == "git" and _is_dangerous_git(tokens):
        return RiskLevel.DENY

    # 6. 纵深防御的子串模式。
    if any(pattern in normalized for pattern in DENY_PATTERNS):
        return RiskLevel.DENY

    # 7. 白名单：只读命令自动执行；验证类工具需确认。
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in SAFE_PREFIXES):
        return RiskLevel.SAFE
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in CONFIRM_PREFIXES):
        return RiskLevel.CONFIRM

    # 8. 兜底：未知命令需显式确认（绝不静默自动执行）。
    return RiskLevel.CONFIRM
