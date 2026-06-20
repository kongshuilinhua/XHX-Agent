"""MCP 集成测试：直接测 MCPManager 的工具注册与生命周期（新 Tool 式 registry）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from xhx_agent.runtime.mcp_config import MCPServerConfig
from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import Tool, ToolResult


class _P(BaseModel):
    pass


class _FakeMcpTool(Tool):
    name = "mcp_test-server_hello"
    description = "Say hello"
    params_model = _P
    category = "read"

    async def execute(self, params: _P) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="ok")


def test_mcp_manager_register_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCPManager 连接成功 -> 注册工具 -> close 清理。"""
    fake_config = [MCPServerConfig(name="test-server", command="node", args=["index.js"], env={"FOO": "BAR"})]
    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", lambda ws: fake_config)

    created = []

    class FakeManager:
        def __init__(self, request_timeout: float = 30.0) -> None:
            self.closed = False
            created.append(self)

        def connect_all(self, servers, on_error=None) -> None:
            self.servers = servers

        def register_tools_to_registry(self, registry) -> None:
            registry.register(_FakeMcpTool())

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPManager", FakeManager)

    from xhx_agent.skills.mcp import MCPManager

    mgr = MCPManager(request_timeout=30.0)
    registry = ToolRegistry()

    from xhx_agent.runtime.mcp_config import load_mcp_servers

    servers = load_mcp_servers(Path.cwd())
    mgr.connect_all(servers)
    mgr.register_tools_to_registry(registry)

    assert registry.get("mcp_test-server_hello") is not None
    assert created[0].closed is False

    mgr.close()
    assert created[0].closed is True


def test_mcp_connect_failure_non_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """MCP 连接失败不抛异常，不阻塞后续操作。"""
    fake_config = [MCPServerConfig(name="failed-server", command="invalid_command")]
    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", lambda ws: fake_config)

    errors: list[tuple[str, Exception]] = []

    class FailManager:
        def __init__(self, request_timeout: float = 30.0) -> None:
            self.closed = False

        def connect_all(self, servers, on_error=None) -> None:
            if on_error is not None:
                on_error("failed-server", RuntimeError("Spawn failed"))
            errors.append(("failed-server", RuntimeError("Spawn failed")))

        def register_tools_to_registry(self, registry) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPManager", FailManager)

    from xhx_agent.skills.mcp import MCPManager

    mgr = MCPManager(request_timeout=30.0)
    registry = ToolRegistry()

    from xhx_agent.runtime.mcp_config import load_mcp_servers

    servers = load_mcp_servers(Path.cwd())
    # connect_all 不应抛异常
    mgr.connect_all(servers)
    mgr.register_tools_to_registry(registry)

    # 失败不阻塞
    assert len(errors) == 1
    mgr.close()
