from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from xhx_agent.runtime.paths import global_xhx_dir, xhx_dir


class MCPServerConfig(BaseModel):
    """单个 MCP server 的配置，支持 stdio（本地子进程）/ http（Streamable HTTP）/ sse（legacy）。"""

    name: str
    transport: Literal["stdio", "http", "sse"] = "stdio"
    # stdio 传输
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    # http / sse 远程传输
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    # 静态认证（本期）：bearer token；值只存 gitignored 的 .xhx/mcp.json 或环境变量，勿入库。
    auth_token: str = ""
    auth_token_env: str = ""

    @model_validator(mode="after")
    def _check_transport(self) -> MCPServerConfig:
        if self.transport == "stdio":
            if not self.command:
                raise ValueError(f"MCP server '{self.name}': stdio 传输需要 'command'")
        else:  # http / sse
            if not self.url:
                raise ValueError(f"MCP server '{self.name}': {self.transport} 传输需要 'url'")
        return self


def load_mcp_servers(workspace: Path) -> list[MCPServerConfig]:
    """加载 MCP server 配置，项目级 `.xhx/mcp.json` 优先，其次全局 `~/.xhx/mcp.json`。

    缺文件或解析失败返回空列表。注意：调用方应传入**原始项目根**（而非隔离 worktree）——
    worktree 只含 git 跟踪文件，gitignored 的 `.xhx/` 不在其中。
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
                # JSON 损坏或格式非法：回落到下一级 / 返回空，不让坏配置炸整轮。
                pass

    return []
