from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult


class Params(BaseModel):
    url: str = Field(description="The URL to fetch")
    prompt: str = Field(
        default="",
        description="Optional instructions on what to extract from the page",
    )


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch the content of a web page and convert it to Markdown. Read-only."
    params_model = Params
    category = "read"

    def __init__(self, workspace: Path | None = None, max_bytes: int = 200_000, **kwargs: Any) -> None:
        self._workspace = workspace or Path.cwd()
        self._max_bytes = max_bytes

    async def execute(self, params: Params) -> ToolResult:
        from xhx_agent.tools.web import web_fetch

        try:
            result_str = web_fetch(
                params.url,
                prompt=params.prompt or None,
                max_bytes=self._max_bytes,
            )
            return ToolResult(output=result_str)
        except Exception as e:
            return ToolResult(
                output=f"Failed to fetch {params.url}: {e}", is_error=True
            )
