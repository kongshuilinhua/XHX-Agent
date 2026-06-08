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

# Executables that must never be auto-classified below DENY: destructive file operations,
# privilege escalation, network fetch-and-run, and interactive shells (which would let the
# model smuggle arbitrary commands past this classifier).
DENY_EXECUTABLES = frozenset(
    {
        # destructive / filesystem mutating
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
        # network fetch / remote shells
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
        # interactive shells / shell-outs
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
        # privilege / system control
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
        "taskkill",
        "reg",
        "regedit",
        "diskpart",
        "fdisk",
        "setx",
    }
)

# Interpreters that can execute arbitrary inline code via these flags.
_CODE_INTERPRETERS = frozenset({"python", "python3", "py", "node", "nodejs", "deno", "perl", "ruby", "php"})
_INLINE_CODE_FLAGS = frozenset({"-c", "-e", "--eval", "--exec"})

# Defense-in-depth substring patterns: caught even when first-token analysis would miss them
# (e.g. dangerous subcommands of otherwise-allowed executables).
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

# Shell metacharacters imply command chaining, redirection, or substitution. We never let
# those through the auto path. Checked against the *raw* command (before whitespace
# normalization) so newlines and semicolons cannot be smuggled in to split commands.
_SHELL_METACHARS = (";", "|", "&", "`", "$(", "${", ">", "<", "\n", "\r")


def _executable_name(token: str) -> str:
    name = Path(token.strip("'\"")).name.lower()
    if "." in name and name.rsplit(".", 1)[1] in {"exe", "cmd", "bat", "com", "ps1"}:
        name = name.rsplit(".", 1)[0]
    return name


def _is_dangerous_git(tokens: list[str]) -> bool:
    args = [t.lower() for t in tokens[1:]]
    if "reset" in args and ("--hard" in args or "--merge" in args):
        return True
    if "clean" in args and any(t.startswith("-") and ("f" in t or "d" in t) for t in args):
        return True
    if "push" in args and ("--force" in args or "-f" in args):
        return True
    return "checkout" in args and "." in args


def classify_command(command: str) -> RiskLevel:
    # 1. Any shell metacharacter (chaining / redirection / substitution) is denied outright.
    if any(char in command for char in _SHELL_METACHARS):
        return RiskLevel.DENY

    normalized = " ".join(command.strip().split()).lower()
    if not normalized:
        return RiskLevel.CONFIRM

    # 2. Tokenize so classification matches how a shell would parse the command, instead of
    #    fragile substring matching that misses flag reorderings (rm -fr, rm -r -f, ...).
    try:
        tokens = shlex.split(normalized, posix=(os.name != "nt"))
    except ValueError:
        return RiskLevel.DENY
    if not tokens:
        return RiskLevel.CONFIRM

    exe = _executable_name(tokens[0])

    # 3. Denylisted executable as the command itself.
    if exe in DENY_EXECUTABLES:
        return RiskLevel.DENY

    # 4. Interpreter executing arbitrary inline code.
    if exe in _CODE_INTERPRETERS and any(arg in _INLINE_CODE_FLAGS for arg in (a.lower() for a in tokens[1:])):
        return RiskLevel.DENY

    # 5. Dangerous git subcommands (history/working-tree destruction).
    if exe == "git" and _is_dangerous_git(tokens):
        return RiskLevel.DENY

    # 6. Defense-in-depth substring patterns.
    if any(pattern in normalized for pattern in DENY_PATTERNS):
        return RiskLevel.DENY

    # 7. Allowlist: read-only commands auto-execute; verification tools require confirmation.
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in SAFE_PREFIXES):
        return RiskLevel.SAFE
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in CONFIRM_PREFIXES):
        return RiskLevel.CONFIRM

    # 8. Default: unknown command requires explicit confirmation (never silently auto-runs).
    return RiskLevel.CONFIRM
