from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
from collections.abc import Callable
from typing import Any


def read_line_with_timeout(stream: Any, timeout: float = 5.0) -> str:
    """Read a single line from stream in a separate thread with timeout."""
    q: queue.Queue[tuple[str | None, Exception | None]] = queue.Queue()

    def reader():
        try:
            line = stream.readline()
            q.put((line, None))
        except Exception as e:
            q.put((None, e))

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    try:
        line, err = q.get(timeout=timeout)
        if err:
            raise err
        return line or ""
    except queue.Empty:
        raise TimeoutError("Reading from MCP server timed out")


class MCPClient:
    def __init__(
        self,
        command: list[str] | None = None,
        is_mock: bool = False,
        server_name: str | None = None,
        allow_mock: bool = False,
        env: dict[str, str] | None = None,
    ) -> None:
        self.command = command
        self.server_name = server_name
        self.allow_mock = allow_mock
        self.env = env
        self.is_mock = is_mock or (allow_mock and not command)
        self.process: subprocess.Popen | None = None
        self._next_id = 1
        self._mock_tools: list[dict[str, Any]] = [
            {
                "name": "mcp_fetch_weather",
                "description": "Get current weather for a city",
                "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
            {
                "name": "mcp_calculate",
                "description": "Run mathematical calculation",
                "inputSchema": {
                    "type": "object",
                    "properties": {"expression": {"type": "string"}},
                    "required": ["expression"],
                },
            },
        ]

    def connect(self) -> None:
        if self.is_mock or not self.command:
            if not self.allow_mock:
                raise ValueError("No command specified and mock is disabled")
            return

        try:
            merged_env = dict(os.environ)
            if self.env:
                merged_env.update(self.env)
            # Start MCP server subprocess
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=merged_env,
            )
            # Initialize handshake
            self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "xhx-agent", "version": "1.0.0"},
                },
            )
            # Send initialized notification
            self._send_notification("initialized", {})
        except Exception as e:
            if not self.allow_mock:
                raise RuntimeError(f"Failed to connect to MCP server: {e}")
            # Fallback to mock on connection error
            self.is_mock = True
            self.process = None

    def close(self) -> None:
        if self.process:
            self.process.terminate()
            self.process.wait()
            self.process = None

    def _send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.is_mock or not self.process or not self.process.stdin or not self.process.stdout:
            return {}

        req_id = self._next_id
        self._next_id += 1

        msg = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}

        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()

            line = read_line_with_timeout(self.process.stdout, timeout=5.0)
            if not line:
                raise OSError("Empty response from MCP server stdout")
            return json.loads(line)
        except Exception as e:
            if not self.allow_mock:
                raise RuntimeError(f"MCP request failed: {e}")
            # Fallback
            self.is_mock = True
            return {}

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        if self.is_mock or not self.process or not self.process.stdin:
            return
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        try:
            self.process.stdin.write(json.dumps(msg) + "\n")
            self.process.stdin.flush()
        except Exception:
            pass

    def list_tools(self) -> list[dict[str, Any]]:
        if self.is_mock:
            if self.server_name:
                tools = []
                for t in self._mock_tools:
                    orig = t["name"][4:] if t["name"].startswith("mcp_") else t["name"]
                    tools.append(
                        {
                            "name": f"mcp_{self.server_name}_{orig}",
                            "original_name": orig,
                            "description": t.get("description", ""),
                            "inputSchema": t.get("inputSchema", {}),
                        }
                    )
                return tools
            return self._mock_tools

        res = self._send_request("tools/list", {})
        if "result" in res and "tools" in res["result"]:
            tools = []
            for t in res["result"]["tools"]:
                name = t["name"]
                if self.server_name:
                    tool_name = f"mcp_{self.server_name}_{name}"
                else:
                    tool_name = f"mcp_{name}" if not name.startswith("mcp_") else name
                tools.append(
                    {
                        "name": tool_name,
                        "original_name": t["name"],
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                )
            return tools
        if not self.allow_mock:
            return []
        return self._mock_tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.is_mock:
            tool_key = name
            if self.server_name and name.startswith(f"mcp_{self.server_name}_"):
                tool_key = f"mcp_{name[len(f'mcp_{self.server_name}_') :]}"

            if tool_key == "mcp_fetch_weather":
                city = arguments.get("city", "Beijing")
                return {"content": [{"type": "text", "text": f"The weather in {city} is sunny, 22°C."}]}
            if tool_key == "mcp_calculate":
                expr = arguments.get("expression", "2+2")
                try:
                    import ast
                    import operator

                    safe_binops: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
                        ast.Add: operator.add,
                        ast.Sub: operator.sub,
                        ast.Mult: operator.mul,
                        ast.Div: operator.truediv,
                    }
                    safe_unaryops: dict[type[ast.unaryop], Callable[[Any], Any]] = {
                        ast.USub: operator.neg,
                        ast.UAdd: lambda x: x,
                    }

                    def _eval_node(node: ast.AST) -> Any:
                        if isinstance(node, ast.Expression):
                            return _eval_node(node.body)
                        elif isinstance(node, ast.Constant):
                            if isinstance(node.value, (int, float)):
                                return node.value
                            raise TypeError("Only numeric constants allowed")
                        elif isinstance(node, ast.BinOp):
                            left = _eval_node(node.left)
                            right = _eval_node(node.right)
                            binop_type = type(node.op)
                            if binop_type in safe_binops:
                                return safe_binops[binop_type](left, right)
                            raise NotImplementedError(f"Operator {binop_type} not supported")
                        elif isinstance(node, ast.UnaryOp):
                            operand = _eval_node(node.operand)
                            unaryop_type = type(node.op)
                            if unaryop_type in safe_unaryops:
                                return safe_unaryops[unaryop_type](operand)
                            raise NotImplementedError(f"Unary operator {unaryop_type} not supported")
                        raise TypeError(f"Unsupported AST node type: {type(node)}")

                    tree = ast.parse(expr, mode="eval")
                    val = _eval_node(tree)
                    return {"content": [{"type": "text", "text": str(val)}]}
                except Exception as e:
                    return {"content": [{"type": "text", "text": f"Error: {e}"}]}
            return {"content": [{"type": "text", "text": f"Mock tool {name} executed."}]}

        original_name = name
        if self.server_name:
            prefix = f"mcp_{self.server_name}_"
            if name.startswith(prefix):
                original_name = name[len(prefix) :]
        else:
            if name.startswith("mcp_"):
                original_name = name[4:]

        res = self._send_request("tools/call", {"name": original_name, "arguments": arguments})
        if "result" in res:
            return res["result"]
        if "error" in res:
            return {"isError": True, "content": [{"type": "text", "text": str(res["error"])}]}
        return {"content": [{"type": "text", "text": f"Error calling tool {name}"}]}

    def register_tools_to_registry(self, registry: Any) -> None:
        """Register MCP tools to ToolRegistry as runnable tools."""
        from xhx_agent.tools.registry import ToolContext, ToolDefinition, ToolExecutionResult

        tools = self.list_tools()
        for t in tools:
            tool_name = t["name"]
            input_schema = t.get("inputSchema", {})
            description = t.get("description", "")

            # Define runner closure
            def make_runner(name: str):
                def runner(context: ToolContext, arguments: dict[str, object]) -> ToolExecutionResult:
                    try:
                        res = self.call_tool(name, arguments)
                        is_error = res.get("isError", False)
                        content_list = res.get("content", [])
                        text_outputs = []
                        for item in content_list:
                            if item.get("type") == "text":
                                text_outputs.append(item.get("text", ""))
                        summary = "\n".join(text_outputs) or f"Tool {name} completed."

                        return ToolExecutionResult(
                            tool=name,
                            status="failed" if is_error else "success",
                            summary=summary,
                            trace_payload={"tool": name, "arguments": arguments, "result": res},
                            evidence_kind="decision",
                            evidence_source=name,
                            evidence_summary=summary,
                        )
                    except Exception as e:
                        return ToolExecutionResult(
                            tool=name,
                            status="failed",
                            summary=f"Error executing MCP tool {name}: {e}",
                            trace_payload={"tool": name, "arguments": arguments, "error": str(e)},
                            error=str(e),
                        )

                return runner

            # Register as ToolDefinition
            d = ToolDefinition(
                name=tool_name,
                description=description or f"MCP tool {tool_name}",
                parameters=input_schema or {"type": "object", "properties": {}},
                runner=make_runner(tool_name),
            )
            registry.register_definition(d)
