"""一个真实的最小 MCP server（stdio），仅供测试用。

通过 `python tests/mcp_fake_server.py` 以 stdio 传输启动，暴露两个工具。
不是测试文件（不以 test_ 开头），pytest 不会收集它。
"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("fake")


@mcp.tool()
def echo(text: str) -> str:
    return f"echo: {text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    return a + b


if __name__ == "__main__":
    mcp.run()
