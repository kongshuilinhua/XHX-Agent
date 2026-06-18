from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult


class Params(BaseModel):
    query: str = Field(description="The search query")


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for query and return search results. Read-only."
    params_model = Params
    category = "read"

    def __init__(self, workspace: Path | None = None, **kwargs: Any) -> None:
        from xhx_agent.runtime.config import load_config

        ws = workspace or Path.cwd()
        try:
            cfg = load_config(ws)
            api_key = cfg.web_search.tavily_api_key
            if not api_key:
                env_var = cfg.web_search.tavily_api_key_env or "TAVILY_API_KEY"
                api_key = os.environ.get(env_var, "")
            self._api_key = api_key
            self._max_results = cfg.web_search.max_results
        except Exception:
            self._api_key = os.environ.get("TAVILY_API_KEY", "")
            self._max_results = 5

    async def execute(self, params: Params) -> ToolResult:
        if not self._api_key:
            return ToolResult(
                output="未配置 Tavily API key。请在 .xhx/config.json 中设置 web_search.tavily_api_key。",
                is_error=True,
            )

        try:
            from xhx_agent.tools.web import web_search

            results = web_search(params.query, self._api_key, max_results=self._max_results)

            lines = []
            for idx, item in enumerate(results, 1):
                lines.append(f"### {idx}. {item.get('title', 'Untitled')}")
                lines.append(f"URL: {item.get('url', 'N/A')}")
                lines.append(f"Snippet: {item.get('content', '')}\n")
            summary = "\n".join(lines) or "No results found."

            return ToolResult(output=summary)
        except Exception as e:
            return ToolResult(output=f"Web search failed: {e}", is_error=True)
