from pathlib import Path

import pytest

from xhx_agent.runtime.app import RuntimeApp
from xhx_agent.runtime.mcp_config import MCPServerConfig


def test_mcp_integration_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".xhx").mkdir()
    app = RuntimeApp(workspace=tmp_path)
    app.init_project()

    fake_config = [MCPServerConfig(name="test-server", command="node", args=["index.js"], env={"FOO": "BAR"})]

    captured: dict[str, object] = {}

    def fake_load(ws: Path) -> list[MCPServerConfig]:
        captured["ws"] = ws
        return fake_config

    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", fake_load)

    created = []

    class FakeManager:
        def __init__(self, request_timeout: float = 30.0) -> None:
            self.closed = False
            created.append(self)

        def connect_all(self, servers, on_error=None) -> None:
            self.servers = servers

        def register_tools_to_registry(self, registry) -> None:
            from xhx_agent.tools.registry import ToolDefinition, ToolExecutionResult

            registry.register_definition(
                ToolDefinition(
                    name="mcp_test-server_hello",
                    description="Say hello",
                    parameters={"type": "object", "properties": {}},
                    runner=lambda c, a: ToolExecutionResult(
                        tool="mcp_test-server_hello", status="success", summary="ok", trace_payload={}
                    ),
                )
            )

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPManager", FakeManager)

    from xhx_agent.models.types import ModelPlan

    app._build_plan = lambda *args, **kwargs: ModelPlan(summary="Done", status="done", steps=[])

    result = app.run_task("do nothing", profile_name="mock", mode="linear")

    # 工具注册成功（fake close 不注销，便于断言）
    assert "mcp_test-server_hello" in app.tool_registry.names
    # 生命周期 close 被调
    assert created[0].closed is True
    # 配置从 original_workspace（项目根）加载，而非 worktree
    assert captured["ws"] == tmp_path.resolve()
    assert result.status == "success"


def test_mcp_integration_connect_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".xhx").mkdir()
    app = RuntimeApp(workspace=tmp_path)
    app.init_project()

    fake_config = [MCPServerConfig(name="failed-server", command="invalid_command")]
    monkeypatch.setattr("xhx_agent.runtime.mcp_config.load_mcp_servers", lambda ws: fake_config)

    class FailManager:
        def __init__(self, request_timeout: float = 30.0) -> None:
            self.closed = False

        def connect_all(self, servers, on_error=None) -> None:
            if on_error is not None:
                on_error("failed-server", RuntimeError("Spawn failed"))

        def register_tools_to_registry(self, registry) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr("xhx_agent.skills.mcp.MCPManager", FailManager)

    from xhx_agent.models.types import ModelPlan

    app._build_plan = lambda *args, **kwargs: ModelPlan(summary="Done", status="done", steps=[])

    events = []
    result = app.run_task("do nothing", profile_name="mock", event_callback=events.append, mode="linear")

    # 失败不阻塞执行
    assert result.status == "success"
    # 发了 mcp_server_failed 事件
    assert "mcp_server_failed" in [e.type for e in events]
    # 内置工具仍可用
    assert "search" in app.tool_registry.names
