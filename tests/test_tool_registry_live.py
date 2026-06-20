"""tools/__init__.py 的 live ToolRegistry 单测：注册/启停/schema/deferred 搜索。"""

from __future__ import annotations

from pydantic import BaseModel

from xhx_agent.tools import ToolRegistry
from xhx_agent.tools.base import Tool, ToolResult


class _P(BaseModel):
    x: str = ""


class _ReadTool(Tool):
    name = "reader"
    description = "reads stuff"
    params_model = _P
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: _P) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="r")


class _DeferredTool(Tool):
    name = "WebSearch"
    description = "search the web for information"
    params_model = _P
    category = "read"
    should_defer = True

    async def execute(self, params: _P) -> ToolResult:  # type: ignore[override]
        return ToolResult(output="d")


def _reg() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_ReadTool())
    reg.register(_DeferredTool())
    return reg


def test_register_get_enable_disable() -> None:
    reg = _reg()
    assert reg.get("reader") is not None
    assert reg.is_enabled("reader") is True
    reg.disable("reader")
    assert reg.is_enabled("reader") is False
    reg.enable("reader")
    assert reg.is_enabled("reader") is True
    reg.enable_all()
    assert reg.is_enabled("reader") is True
    names = [t.name for t in reg.list_tools()]
    assert "reader" in names and "WebSearch" in names


def test_unregister() -> None:
    reg = _reg()
    reg.unregister("reader")
    assert reg.get("reader") is None


def test_get_all_schemas_excludes_deferred_and_disabled() -> None:
    reg = _reg()
    schemas = reg.get_all_schemas("openai-compat")
    names = {s["function"]["name"] for s in schemas}
    assert "reader" in names
    # deferred 未被发现 → 不在常规 schema 列表里
    assert "WebSearch" not in names
    # 禁用后也不在
    reg.disable("reader")
    names2 = {s["function"]["name"] for s in reg.get_all_schemas("openai-compat")}
    assert "reader" not in names2


def test_deferred_discovery_and_search() -> None:
    reg = _reg()
    # 未发现时出现在 deferred 名单
    assert "WebSearch" in reg.get_deferred_tool_names()
    # 关键词搜索能命中
    results = reg.search_deferred("search web", max_results=5, protocol="openai")
    assert any(r["function"]["name"] == "WebSearch" for r in results)
    # 按名查找
    found = reg.find_deferred_by_names(["WebSearch"], protocol="openai")
    assert found and found[0]["function"]["name"] == "WebSearch"
    # 标记已发现后从 deferred 名单移除
    reg.mark_discovered("WebSearch")
    assert reg.is_discovered("WebSearch") is True
    assert "WebSearch" not in reg.get_deferred_tool_names()
    # 发现后进入常规 schema
    names = {s["function"]["name"] for s in reg.get_all_schemas("openai-compat")}
    assert "WebSearch" in names


def test_anthropic_schema_shape() -> None:
    reg = _reg()
    schemas = reg.get_all_schemas("anthropic")
    reader = [s for s in schemas if s.get("name") == "reader"]
    assert reader and "input_schema" in reader[0]
