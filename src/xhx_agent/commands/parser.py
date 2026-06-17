"""斜杠命令解析器。"""

from __future__ import annotations


def parse_command(line: str) -> tuple[str, str]:
    """解析命令行为 (command, argument)。

    Examples:
        "/model deepseek" → ("/model", "deepseek")
        "/help"          → ("/help", "")
        "/mode team"     → ("/mode", "team")
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return ("", "")
    parts = stripped.split(" ", 1)
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument
