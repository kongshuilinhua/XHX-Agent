from pathlib import Path
from typing import Any

import pytest

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.mcp_config import MCPServerConfig
from xhx_agent.skills.mcp import MCPClient


def test_mcp_integration_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Setup test workspace
    (tmp_path / ".xhx").mkdir()
    app = RuntimeApp(workspace=tmp_path)
    app.init_project()

    # Fake server config
    fake_config = [
        MCPServerConfig(name="test-server", command="node", args=["index.js"], env={"FOO": "BAR"}, transport="stdio")
    ]

    # Mock load_mcp_servers to return our config
    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", lambda ws: fake_config)

    # Track close calls
    close_called = False

    class FakeMCPClient(MCPClient):
        def connect(self) -> None:
            # Succeeds
            pass

        def close(self) -> None:
            nonlocal close_called
            close_called = True

        def list_tools(self) -> list[dict[str, Any]]:
            return [
                {
                    "name": "mcp_test-server_hello",
                    "original_name": "hello",
                    "description": "Say hello",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]

    # Monkeypatch MCPClient creation
    monkeypatch.setattr("xhx_agent.skills.mcp.MCPClient", FakeMCPClient)

    # Mock CoderAgent/Orchestrator to avoid hitting actual LLMs/completing loop immediately
    from xhx_agent.models.types import ModelPlan

    app._build_plan = lambda *args, **kwargs: ModelPlan(summary="Done", status="done", steps=[])

    # Run the task
    result = app.run_task("do nothing", profile_name="mock", mode="linear")

    # Check that tools are registered
    assert "mcp_test-server_hello" in app.tool_registry.names
    assert close_called is True
    assert result.status == "success"


def test_mcp_integration_connect_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".xhx").mkdir()
    app = RuntimeApp(workspace=tmp_path)
    app.init_project()

    fake_config = [MCPServerConfig(name="failed-server", command="invalid_command", args=[], env={}, transport="stdio")]
    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", lambda ws: fake_config)

    class FailedMCPClient(MCPClient):
        def connect(self) -> None:
            raise RuntimeError("Spawn failed")

        def close(self) -> None:
            pass

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPClient", FailedMCPClient)

    from xhx_agent.models.types import ModelPlan

    app._build_plan = lambda *args, **kwargs: ModelPlan(summary="Done", status="done", steps=[])

    events = []
    result = app.run_task("do nothing", profile_name="mock", event_callback=events.append, mode="linear")

    # Check that failure did not block execution
    assert result.status == "success"
    # Event mcp_server_failed should be emitted
    event_types = [e.type for e in events]
    assert "mcp_server_failed" in event_types
    # Inner tools are still available
    assert "search" in app.tool_registry.names
