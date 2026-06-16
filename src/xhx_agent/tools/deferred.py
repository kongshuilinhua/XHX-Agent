"""延迟工具发现：大量工具默认隐藏，按查询关键词动态暴露给模型。

来源：mewcode tools/ 中的 deferred tool 概念。
"""

from __future__ import annotations

from typing import Any


def search_deferred(
    registry: Any,
    query: str,
    *,
    max_results: int = 10,
) -> list[dict[str, Any]]:
    """在注册表中搜索与 *query* 匹配的隐藏工具。

    匹配逻辑：query 中的任意词出现在工具 name 或 description 中即匹配。
    用于 ToolSearch 工具实现。
    """
    if not query or not query.strip():
        return []

    keywords = query.lower().split()
    results: list[dict[str, Any]] = []

    all_schemas = registry.tool_schemas(include_deferred=True) if hasattr(registry, 'tool_schemas') else []
    for schema in all_schemas:
        name = schema.get("name", "")
        desc = schema.get("description", "")
        combined = f"{name} {desc}".lower()

        if any(kw in combined for kw in keywords):
            results.append({
                "name": name,
                "description": desc,
                "schema": schema,
            })

    return results[:max_results]
