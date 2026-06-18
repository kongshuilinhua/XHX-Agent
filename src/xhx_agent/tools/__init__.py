"""工具注册表 + 内置工具工厂。

所有工具都继承 Tool ABC（tools/base.py），通过 ToolRegistry 注册。
支持延迟加载（should_defer）和 ToolSearch 按需发现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from xhx_agent.tools.base import Tool

if TYPE_CHECKING:
    from xhx_agent.cache import FileCache


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """移除一个已注册工具。MCP server 关闭时用，避免共享 registry 残留陈旧定义。"""
        self._tools.pop(name, None)
        self._disabled.discard(name)
        self._discovered.discard(name)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def is_enabled(self, name: str) -> bool:
        return name in self._tools and name not in self._disabled

    def enable(self, name: str) -> None:
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        if name in self._tools:
            self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()

    def mark_discovered(self, name: str) -> None:
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered

    def get_deferred_tool_names(self) -> list[str]:
        return [
            name
            for name, tool in self._tools.items()
            if getattr(tool, "should_defer", False) and name not in self._discovered and name not in self._disabled
        ]

    def search_deferred(self, query: str, max_results: int, protocol: str = "anthropic") -> list[dict[str, Any]]:
        query_lower = query.lower()
        scored: list[tuple[int, str, Tool]] = []
        for name, tool in self._tools.items():
            if not getattr(tool, "should_defer", False):
                continue
            if name in self._disabled:
                continue
            score = 0
            name_lower = name.lower()
            desc_lower = (tool.description or "").lower()
            if query_lower in name_lower:
                score += 10
            if query_lower in desc_lower:
                score += 5
            for word in query_lower.split():
                if word in name_lower:
                    score += 3
                if word in desc_lower:
                    score += 1
            if score > 0:
                scored.append((score, name, tool))
        scored.sort(key=lambda x: x[0], reverse=True)
        results: list[dict[str, Any]] = []
        for _, _name, tool in scored[:max_results]:
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append(
                    {
                        "type": "function",
                        "function": {
                            "name": base["name"],
                            "description": base["description"],
                            "parameters": base["input_schema"],
                        },
                    }
                )
            else:
                results.append(base)
        return results

    def find_deferred_by_names(self, names: list[str], protocol: str = "anthropic") -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            if not getattr(tool, "should_defer", False):
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                results.append(
                    {
                        "type": "function",
                        "function": {
                            "name": base["name"],
                            "description": base["description"],
                            "parameters": base["input_schema"],
                        },
                    }
                )
            else:
                results.append(base)
        return results

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get_all_schemas(self, protocol: str = "openai-compat") -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for name, tool in self._tools.items():
            if name in self._disabled:
                continue
            if getattr(tool, "should_defer", False) and name not in self._discovered:
                continue
            base = tool.get_schema()
            if protocol in ("openai", "openai-compat"):
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": base["name"],
                            "description": base["description"],
                            "parameters": base["input_schema"],
                        },
                    }
                )
            else:
                schemas.append(base)
        return schemas


def create_default_registry(
    file_cache: FileCache | None = None,
    file_history: Any = None,
    workspace: str | None = None,
) -> ToolRegistry:
    from pathlib import Path

    from xhx_agent.tools.apply_patch_tool import ApplyPatchTool
    from xhx_agent.tools.bash import Bash
    from xhx_agent.tools.edit_file import EditFile
    from xhx_agent.tools.glob import Glob
    from xhx_agent.tools.grep import Grep
    from xhx_agent.tools.impl.tool_search import ToolSearchTool
    from xhx_agent.tools.present_plan import PresentPlanTool
    from xhx_agent.tools.read_file import ReadFile
    from xhx_agent.tools.repo_query import RepoQueryTool
    from xhx_agent.tools.web_fetch_tool import WebFetchTool
    from xhx_agent.tools.web_search_tool import WebSearchTool
    from xhx_agent.tools.write_file import WriteFile

    ws = Path(workspace) if workspace else Path.cwd()

    registry = ToolRegistry()

    # 只读工具
    registry.register(ReadFile(file_cache=file_cache))
    registry.register(Glob())
    registry.register(Grep())
    registry.register(PresentPlanTool())
    registry.register(RepoQueryTool(workspace=str(ws)))
    registry.register(WebFetchTool(workspace=ws))
    registry.register(WebSearchTool(workspace=ws))

    # 写入工具
    registry.register(WriteFile(file_cache=file_cache, file_history=file_history))
    registry.register(EditFile(file_cache=file_cache, file_history=file_history))
    registry.register(ApplyPatchTool(workspace=ws))

    # 命令工具
    registry.register(Bash())

    # 延迟发现的工具（MCP 等）
    registry.register(ToolSearchTool(registry, protocol="openai-compat"))

    return registry
