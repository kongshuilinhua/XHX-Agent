"""MCP 接线回归。

run_textual_console 此前构造 XHXApp 时没加载 .xhx/mcp.json、没传 mcp_servers，
导致 MCP 客户端虽完整却永不连接。本测试验证：配置能端到端读取，且 app 收到
mcp_servers 后 _init_mcp 真的把这些配置喂给 connect_all 并把 MCP 工具注册进 registry。
"""

from __future__ import annotations

import asyncio
import json

from pydantic import BaseModel

from xhx_agent.config import ProviderConfig
from xhx_agent.tools.base import Tool, ToolResult


class _P(BaseModel):
    pass


class _FakeMcpTool(Tool):
    name = "mcp__fs__hello"
    description = "hi"
    params_model = _P
    category = "read"

    async def execute(self, params: _P) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="ok")


def test_mcp_config_loaded_and_wired(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    from xhx_agent.runtime.init import init_project

    init_project(tmp_path)
    (tmp_path / ".xhx" / "mcp.json").write_text(
        json.dumps({"servers": [{"name": "fs", "command": "node", "args": ["x.js"]}]}),
        encoding="utf-8",
    )

    # 1) 配置能端到端读取
    from xhx_agent.runtime.mcp_config import load_mcp_servers

    servers = load_mcp_servers(tmp_path)
    assert len(servers) == 1 and servers[0].name == "fs"

    # 2) app 收到 mcp_servers 后 _init_mcp 把配置喂给 connect_all 并注册工具
    captured: dict = {}

    class FakeManager:
        def __init__(self, request_timeout: float = 30.0) -> None:
            pass

        def connect_all(self, servers, on_error=None) -> None:
            captured["servers"] = servers

        def register_tools_to_registry(self, registry) -> None:
            registry.register(_FakeMcpTool())

        def close(self) -> None:
            pass

    import xhx_agent.tui.app as app_mod

    monkeypatch.setattr(app_mod, "MCPManager", FakeManager)

    from xhx_agent.tui.app import XHXApp

    provider = ProviderConfig(name="mock", protocol="mock", base_url="", model="mock", api_key="x")

    async def _run() -> bool:
        app = XHXApp(providers=[provider], mcp_servers=servers)
        async with app.run_test() as pilot:
            await pilot.pause()
            if app._mcp_init_task is not None:
                await app._mcp_init_task
            await pilot.pause()
            return app.registry.get("mcp__fs__hello") is not None

    registered = asyncio.run(_run())
    assert captured.get("servers") == servers  # 接线通：_init_mcp 把配置喂给了 connect_all
    assert registered  # MCP 工具真注册进 registry


def test_mcp_instructions_reinjected_after_conversation_reset() -> None:
    """MCP server 说明按"当前对话里是否已有"注入（自愈式）。

    回归：曾用一次性标志 _mcp_instructions_ok，/new、恢复会话、压缩清史后
    新对话里永远不会再注入 MCP 说明。
    """
    from xhx_agent.conversation import ConversationManager, Message
    from xhx_agent.tui.app import _has_mcp_instructions

    instructions = "# MCP Server Instructions\n\n## github\nAvailable tools: mcp_github_get_me"

    conv = ConversationManager()
    assert not _has_mcp_instructions(conv)

    conv.add_system_reminder(instructions)
    assert _has_mcp_instructions(conv)  # 已注入则不再重复

    # /new / 恢复会话：换新 conversation 对象 → 需要重新注入
    assert not _has_mcp_instructions(ConversationManager())

    # 压缩：历史被摘要替换 → 说明随之丢失，需要重新注入
    conv.history = [Message(role="user", content="[对话摘要] 之前讨论了……")]
    assert not _has_mcp_instructions(conv)
