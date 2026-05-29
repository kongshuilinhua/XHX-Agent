from __future__ import annotations

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
    if any(pattern in normalized for pattern in DENY_PATTERNS):
        return RiskLevel.DENY
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in SAFE_PREFIXES):
        return RiskLevel.SAFE
    if any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in CONFIRM_PREFIXES):
        return RiskLevel.CONFIRM
    return RiskLevel.CONFIRM
