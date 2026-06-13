import json
import re


def tool_header(tool: str, arguments: dict) -> str:
    """Formats a tool call into a user-friendly header line using its primary argument."""
    if not arguments:
        arguments = {}

    if tool in ("search", "repo_query"):
        query = arguments.get("query")
        if query is not None:
            return f'{tool} "{query}"'
        return tool

    elif tool == "read_file":
        path = arguments.get("path")
        if path is not None:
            return f"read_file {path}"
        return "read_file"

    elif tool == "terminal":
        command = arguments.get("command")
        if command is not None:
            return f"$ {command}"
        return "$"

    elif tool == "verify":
        command = arguments.get("command")
        if command is not None:
            return f"verify {command}"
        return "verify (default)"

    elif tool == "apply_patch":
        patch = arguments.get("patch")
        if patch:
            # Look for "*** Update File: <file>", "*** Add File: <file>", etc.
            match = re.search(r'\*\*\*\s+(?:Update|Add|Delete)\s+File:\s+(.+)', patch)
            if match:
                return f"apply_patch {match.group(1).strip()}"
            # Look for "+++ b/<file>"
            match_git = re.search(r'\+\+\+\s+b/(.+)', patch)
            if match_git:
                return f"apply_patch {match_git.group(1).strip()}"
        return "apply_patch"

    elif tool == "dispatch":
        agent_type = arguments.get("agent_type") or "explore"
        description = arguments.get("description")
        if description:
            return f"dispatch[{agent_type}] {description}"
        return f"dispatch[{agent_type}]"

    # Fallback: tool_name + compact json of arguments, truncated to ~80 chars
    try:
        compact = json.dumps(arguments, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        compact = str(arguments)

    res = f"{tool} {compact}"
    if len(res) > 80:
        res = res[:77] + "..."
    return res
