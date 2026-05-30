from __future__ import annotations

import re
from enum import StrEnum


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
DENY_PATTERNS = (
    "rm -rf",
    "git reset --hard",
    "git checkout -- .",
    "npm install -g",
    "pip install --global",
    "del /s",
    "rmdir /s",
)


def classify_command(command: str) -> RiskLevel:
    normalized = " ".join(command.strip().split()).lower()
    
    # 1. Expand DENY_PATTERNS with strict word boundaries to avoid collision bugs
    deny_regex = re.compile(
        r"\b(chmod|curl|wget|nc|netcat|bash|sh)\b"
    )
    if deny_regex.search(normalized):
        return RiskLevel.DENY

    if any(pattern in normalized for pattern in DENY_PATTERNS):
        return RiskLevel.DENY

    # 2. Proactively classify any command that does not match a whitelist of safe/verification tools
    # as RiskLevel.DENY when it represents an untrusted shell execution (chaining, pipes, redirections, backticks).
    if any(char in normalized for char in ("|", "&&", "||", "`", "$(")):
        return RiskLevel.DENY

    # 3. Check safe prefixes
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in SAFE_PREFIXES):
        return RiskLevel.SAFE

    # 4. Check confirm prefixes
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in CONFIRM_PREFIXES):
        return RiskLevel.CONFIRM

    return RiskLevel.CONFIRM


