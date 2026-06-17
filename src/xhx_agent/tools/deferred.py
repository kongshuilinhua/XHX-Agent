"""延迟工具发现：大量工具默认隐藏，按查询关键词动态暴露给模型。

来源：mewcode tools/ 中的 deferred tool 概念。

注意：当前 XHX-Agent 暂未启用延迟工具发现机制（所有工具默认直接暴露）。
此模块保留为将来 ToolSearch 实现的接口占位。
"""

from __future__ import annotations

from typing import Any


def search_deferred(
    registry: Any,
    query: str,
    *,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """在注册表中搜索与 *query* 匹配的工具。

    匹配逻辑：query 中的任意词出现在工具 name 或 description 中即匹配。

    当前实现：直接遍历所有已注册工具的 schema（无延迟隐藏机制）。
    将来可扩展为仅在 `_discovered` 集合中的工具中搜索。
    """
    if not query or not query.strip():
        return []

    keywords = query.lower().split()
    results: list[dict[str, Any]] = []

    # tool_schemas() 不接受参数，直接获取全量
    schemas: list[dict[str, Any]] = []
    if hasattr(registry, 'tool_schemas'):
        schemas = registry.tool_schemas()
    elif hasattr(registry, 'schemas'):
        schemas = registry.schemas()

    for schema in schemas:
        # tool_schemas 返回 {"type": "function", "function": {...}} 格式
        func = schema.get("function", schema)
        name = func.get("name", "")
        desc = func.get("description", "")
        combined = f"{name} {desc}".lower()

        if any(kw in combined for kw in keywords):
            results.append({
                "name": name,
                "description": desc,
                "schema": schema,
            })

    return results[:max_results]
