from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any


class MCPClient:
    def __init__(self, command: list[str] | None = None, is_mock: bool = False) -> None:
        self.command = command
        self.is_mock = is_mock or (not command)
        self.process: subprocess.Popen | None = None
        self._next_id = 1
        self._mock_tools = [
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
            return

        try:
            # Start MCP server subprocess
            self.process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
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
        except Exception:
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

            line = self.process.stdout.readline()
            if not line:
                raise OSError("Empty response from MCP server stdout")
            return json.loads(line)
        except Exception:
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
            return self._mock_tools

        res = self._send_request("tools/list", {})
        if "result" in res and "tools" in res["result"]:
            tools = []
            for t in res["result"]["tools"]:
                name = t["name"]
                if not name.startswith("mcp_"):
                    name = f"mcp_{name}"
                tools.append(
                    {
                        "name": name,
                        "original_name": t["name"],
                        "description": t.get("description", ""),
                        "inputSchema": t.get("inputSchema", {}),
                    }
                )
            return tools
        return self._mock_tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.is_mock:
            # Handle mock tool execution
            if name == "mcp_fetch_weather":
                city = arguments.get("city", "Beijing")
                return {"content": [{"type": "text", "text": f"The weather in {city} is sunny, 22°C."}]}
            if name == "mcp_calculate":
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
        from xhx_agent.tools.registry import ToolContext, ToolExecutionResult

        tools = self.list_tools()
        for t in tools:
            tool_name = t["name"]

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

            # Register runner under tool_name (which starts with mcp_)
            registry.register(tool_name, make_runner(tool_name))
