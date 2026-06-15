"""MCP 客户端：基于官方 `mcp` SDK，支持 stdio / Streamable HTTP / SSE 传输。

SDK 是 anyio(async) 的，而本项目运行时是同步的。这里用 `anyio.from_thread.start_blocking_portal()`
起一个后台事件循环线程，把每个 server 的 transport + ClientSession 的 `async with` 用
`portal.wrap_async_context_manager` 挂活成同步可用，对外只暴露同步 API（connect_all / list_tools /
call_tool / close）。所有对 session 的调用都经 `portal.call` 桥回那个 loop。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from contextlib import ExitStack, asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from anyio.from_thread import start_blocking_portal
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

if TYPE_CHECKING:
    from xhx_agent.runtime.mcp_config import MCPServerConfig
    from xhx_agent.tools.registry import ToolRegistry

OnError = Callable[[str, Exception], None]


class MCPManager:
    """管理所有 MCP server 连接，并把它们的工具注册进 ToolRegistry。

    生命周期：connect_all() → register_tools_to_registry() →（运行期 call_tool）→ close()。
    单个 server 连接失败只回调 on_error 并跳过，不影响其余 server 与内置工具。
    """

    def __init__(self, request_timeout: float = 30.0) -> None:
        self.request_timeout = request_timeout
        self._stack: ExitStack | None = None
        self._portal: Any = None
        self._sessions: dict[str, Any] = {}
        self._registry: ToolRegistry | None = None
        self._registered_names: list[str] = []

    # ---------- 生命周期 ----------
    def connect_all(self, servers: list[MCPServerConfig], on_error: OnError | None = None) -> None:
        if not servers:
            return
        self._stack = ExitStack()
        # 后台事件循环线程；ExitStack 在 close() 时按 LIFO 先退会话、再停 portal。
        self._portal = self._stack.enter_context(start_blocking_portal())
        for cfg in servers:
            try:
                session = self._stack.enter_context(self._portal.wrap_async_context_manager(self._open(cfg)))
                self._sessions[cfg.name] = session
            except Exception as e:
                if on_error is not None:
                    on_error(cfg.name, e)

    def close(self) -> None:
        # 先注销注册过的工具：registry 是跨 run 共享的，避免残留指向已关会话的定义。
        if self._registry is not None:
            for name in self._registered_names:
                self._registry.unregister(name)
        self._registered_names = []
        self._registry = None
        self._sessions = {}
        if self._stack is not None:
            try:
                self._stack.close()
            except Exception:
                pass
        self._stack = None
        self._portal = None

    # ---------- 在 portal loop 里运行的 async 部分 ----------
    @asynccontextmanager
    async def _open(self, cfg: MCPServerConfig) -> AsyncIterator[Any]:
        transport_cm = self._build_transport(cfg)
        async with transport_cm as streams:
            read, write = streams[0], streams[1]  # http 是 3 元组，stdio/sse 是 2 元组
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    def _build_transport(self, cfg: MCPServerConfig) -> Any:
        if cfg.transport == "stdio":
            if cfg.command is None:
                raise ValueError(f"stdio server {cfg.name} 缺少 command")
            merged_env = {**os.environ, **(cfg.env or {})}
            return stdio_client(StdioServerParameters(command=cfg.command, args=list(cfg.args), env=merged_env))
        # http / sse 远程
        if cfg.url is None:
            raise ValueError(f"{cfg.transport} server {cfg.name} 缺少 url")
        headers = self._resolve_headers(cfg)
        if cfg.transport == "http":
            return streamablehttp_client(cfg.url, headers=headers or None)
        return sse_client(cfg.url, headers=headers or None)

    def _resolve_headers(self, cfg: MCPServerConfig) -> dict[str, str]:
        """静态认证：auth_token（非空优先）→ auth_token_env 环境变量 → 无（不加 Authorization）。"""
        headers = dict(cfg.headers or {})
        token = cfg.auth_token
        if not token and cfg.auth_token_env:
            token = os.environ.get(cfg.auth_token_env, "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---------- 同步操作（桥接到 portal） ----------
    def list_tools(self, server: str) -> list[Any]:
        session = self._sessions[server]
        result = self._portal.call(session.list_tools)
        return list(result.tools)

    def call_tool(self, server: str, name: str, arguments: dict[str, Any]) -> Any:
        session = self._sessions[server]
        timeout = self.request_timeout

        async def _acall() -> Any:
            return await session.call_tool(name, arguments, read_timeout_seconds=timedelta(seconds=timeout))

        return self._portal.call(_acall)

    # ---------- 工具注册 ----------
    def register_tools_to_registry(self, registry: ToolRegistry) -> None:
        from xhx_agent.tools.registry import ToolContext, ToolDefinition, ToolExecutionResult

        self._registry = registry
        for server_name in self._sessions:
            for tool in self.list_tools(server_name):
                full_name = f"mcp_{server_name}_{tool.name}"
                description = tool.description or f"MCP tool {full_name}"
                parameters = tool.inputSchema or {"type": "object", "properties": {}}
                # 只读提示（MCP annotations.readOnlyHint）的工具标 read_only → 内核放行不弹框；
                # 其余（破坏性或无提示=陌生）保持需确认。
                annotations = getattr(tool, "annotations", None)
                read_only = bool(getattr(annotations, "readOnlyHint", False)) if annotations else False

                def make_runner(srv: str, original: str, fname: str) -> Any:
                    def runner(context: ToolContext, arguments: dict[str, object]) -> ToolExecutionResult:
                        try:
                            result = self.call_tool(srv, original, dict(arguments))
                            texts = [t for t in (getattr(item, "text", None) for item in result.content) if t]
                            summary = "\n".join(texts) or f"Tool {fname} completed."
                            is_error = bool(getattr(result, "isError", False))
                            return ToolExecutionResult(
                                tool=fname,
                                status="failed" if is_error else "success",
                                summary=summary,
                                trace_payload={
                                    "tool": fname,
                                    "arguments": arguments,
                                    "result": result.model_dump(mode="json"),
                                },
                                evidence_kind="decision",
                                evidence_source=fname,
                                evidence_summary=summary,
                            )
                        except Exception as e:
                            return ToolExecutionResult(
                                tool=fname,
                                status="failed",
                                summary=f"Error executing MCP tool {fname}: {e}",
                                trace_payload={"tool": fname, "arguments": arguments, "error": str(e)},
                                error=str(e),
                            )

                    return runner

                registry.register_definition(
                    ToolDefinition(
                        name=full_name,
                        description=description,
                        parameters=parameters,
                        read_only=read_only,
                        runner=make_runner(server_name, tool.name, full_name),
                    )
                )
                self._registered_names.append(full_name)
