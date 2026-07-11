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
from pydantic import BaseModel

if TYPE_CHECKING:
    from xhx_agent.runtime.mcp_config import MCPServerConfig

OnError = Callable[[str, Exception], None]


# 宽松参数模型：MCP 工具的参数 schema 是动态的，用 dict 接收
class _MCPToolParams(BaseModel):
    """接受任意 key-value 的宽松参数模型。"""

    model_config = {"extra": "allow"}


def _make_mcp_tool(
    full_name: str,
    description: str,
    server_name: str,
    tool_name: str,
    mcp_manager: Any,
    is_read_only: bool,
    input_schema: dict[str, Any] | None = None,
) -> Any:
    """为单个 MCP 工具动态创建一个 Tool 子类实例。"""
    from xhx_agent.tools.base import Tool, ToolCategory, ToolResult

    # 闭包变量避免 Python 类体 scoping 陷阱（description = description 在类体里报 NameError）
    _desc = description
    _cat: ToolCategory = "read" if is_read_only else "command"
    _schema = dict(input_schema) if input_schema else None

    class _DynamicMCPTool(Tool):
        name = full_name
        description = _desc
        params_model = _MCPToolParams
        category = _cat

        @property
        def is_read_only(self) -> bool:
            return is_read_only

        def get_schema(self) -> dict[str, Any]:
            # params_model 只是执行侧透传容器，其 json schema 是空对象——发给模型
            # 会让每个 MCP 工具看起来"零参数"。必须原样透传 server 的 inputSchema
            # （含 properties/required），模型才知道怎么填参。server 没提供时才回落。
            if _schema is not None:
                return {"name": self.name, "description": self.description, "input_schema": _schema}
            return super().get_schema()

        async def execute(self, params: BaseModel) -> ToolResult:
            args: dict[str, Any] = {}
            if isinstance(params, BaseModel):
                model_dump = getattr(params, "model_dump", None)
                args = dict(model_dump()) if model_dump else dict(getattr(params, "__dict__", {}))
            try:
                result = mcp_manager.call_tool(server_name, tool_name, args)
                texts = []
                for item in getattr(result, "content", []):
                    text = getattr(item, "text", None)
                    if text:
                        texts.append(text)
                summary = "\n".join(texts) or f"Tool {full_name} completed."
                is_error = bool(getattr(result, "isError", False))
                return ToolResult(output=summary, is_error=is_error)
            except Exception as e:
                return ToolResult(output=f"Error executing MCP tool {full_name}: {e}", is_error=True)

    return _DynamicMCPTool()


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
        self._registry: Any = None
        self._registered_names: list[str] = []
        # server 名 -> 失败原因。on_error 回调之外的统一失败记录，供 TUI/headless/命令
        # 事后上报——此前失败只进回调（且调用方全传 None），"配了没生效"完全无从排查。
        self.failed_servers: dict[str, str] = {}

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
                self.failed_servers[cfg.name] = f"{type(e).__name__}: {e}"
                if on_error is not None:
                    on_error(cfg.name, e)

    def close(self) -> None:
        # 先注销注册过的工具：registry 是跨 run 共享的，避免残留指向已关会话的定义。
        if self._registry is not None:
            for name in self._registered_names:
                if hasattr(self._registry, "unregister"):
                    self._registry.unregister(name)
        self._registered_names = []
        self._registry = None
        self._sessions = {}
        self.failed_servers = {}
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
    def register_tools_to_registry(self, registry: Any) -> None:
        """将连接到的所有 MCP 服务器工具注册进 Tool 式 registry。

        为每个 MCP 工具动态构造 Tool 子类实例，用 ``registry.register(tool)``
        注册。MCP 标注的 readOnlyHint 映射到 Tool 实例属性。
        """
        self._registry = registry
        for server_name in self._sessions:
            for tool in self.list_tools(server_name):
                full_name = f"mcp_{server_name}_{tool.name}"
                description = tool.description or f"MCP tool {full_name}"
                annotations = getattr(tool, "annotations", None)
                read_only = bool(getattr(annotations, "readOnlyHint", False)) if annotations else False

                tool_instance = _make_mcp_tool(
                    full_name=full_name,
                    description=description,
                    server_name=server_name,
                    tool_name=tool.name,
                    mcp_manager=self,
                    is_read_only=read_only,
                    input_schema=getattr(tool, "inputSchema", None),
                )
                registry.register(tool_instance)
                self._registered_names.append(full_name)
