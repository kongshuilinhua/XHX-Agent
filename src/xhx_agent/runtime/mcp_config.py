from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from xhx_agent.runtime.paths import global_xhx_dir, xhx_dir


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""

    name: str
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    transport: Literal["stdio"] = "stdio"


def load_mcp_servers(workspace: Path) -> list[MCPServerConfig]:
    """Load MCP server configs, project-level first, falling back to global.

    If no config file is found, returns an empty list.
    """
    project_path = xhx_dir(workspace) / "mcp.json"
    global_path = global_xhx_dir() / "mcp.json"

    for path in (project_path, global_path):
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                servers = data.get("servers", [])
                return [MCPServerConfig.model_validate(s) for s in servers]
            except Exception:
                # If invalid json or format, fall back or ignore
                pass

    return []
