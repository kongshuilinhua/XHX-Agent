"""斜杠命令解析器。"""

from __future__ import annotations


def parse_command(line: str) -> tuple[str, str, bool]:
    """解析命令行为 (command, argument, is_command)。

    Examples:
        "/model deepseek" → ("model", "deepseek", True)
        "/help"          → ("help", "", True)
        "hello"          → ("hello", "", False)
    """
    stripped = line.strip()
    if not stripped.startswith("/"):
        return (stripped, "", False)

    parts = stripped[1:].split(" ", 1)  # 去掉开头的 /
    command = parts[0]
    argument = parts[1].strip() if len(parts) > 1 else ""
    return command, argument, True
