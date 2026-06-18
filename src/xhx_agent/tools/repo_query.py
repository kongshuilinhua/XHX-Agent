from __future__ import annotations

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult


class Params(BaseModel):
    query: str = Field(description="搜索的符号或引用名称")
    kind: str = Field(
        default="symbol",
        description="查询类型：'symbol' 查定义，'reference' 查引用",
    )
    limit: int = Field(default=20, description="最大返回结果数")


class RepoQueryTool(Tool):
    name = "repo_query"
    description = "Query symbol definitions or references in the repository index. Read-only."
    params_model = Params
    category = "read"

    def __init__(self, workspace: str = ".", **kwargs: Any) -> None:
        self._workspace = workspace

    async def execute(self, params: Params) -> ToolResult:
        from pathlib import Path

        from xhx_agent.repo_intel.index import load_repo_intel_index
        from xhx_agent.repo_intel.references import search_references
        from xhx_agent.repo_intel.symbols import search_symbols

        try:
            index = load_repo_intel_index(Path(self._workspace))
        except Exception as e:
            return ToolResult(
                output=f"Failed to load repository index: {e}. "
                "Try running 'xhx init' first.",
                is_error=True,
            )

        if params.kind == "symbol":
            symbols = search_symbols(index.symbol_index, params.query, limit=params.limit)
            if not symbols:
                return ToolResult(output="No matching symbols found.")
            text = "\n".join(
                f"{s.path}:{s.line}  {s.name} ({s.kind})" for s in symbols
            )
            return ToolResult(output=text)
        else:
            references = search_references(
                index.reference_index, params.query, limit=params.limit
            )
            if not references:
                return ToolResult(output="No matching references found.")
            text = "\n".join(
                f"{r.path}:{r.line}  {r.name}: {r.excerpt}" for r in references
            )
            return ToolResult(output=text)
