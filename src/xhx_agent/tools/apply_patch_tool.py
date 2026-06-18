from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from xhx_agent.tools.base import Tool, ToolResult


class Params(BaseModel):
    patch: str = Field(description="完整 unified diff patch 文本")


class ApplyPatchTool(Tool):
    name = "apply_patch"
    description = (
        "对工作区文件进行增量修改或创建新文件。支持标准 unified diff 格式。\n"
        "修改文件示例：\n"
        "--- a/src/utils.py\n"
        "+++ b/src/utils.py\n"
        "@@ -10,6 +10,6 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )
    params_model = Params
    category = "write"

    def __init__(self, workspace: Path | None = None, **kwargs: Any) -> None:
        self._workspace = workspace

    async def execute(self, params: Params) -> ToolResult:
        from xhx_agent.tools.patch import apply_patch

        workspace = self._workspace or Path.cwd()
        result = apply_patch(workspace, params.patch)

        if result.status == "success":
            return ToolResult(output=f"Successfully applied patch. Changed files: {', '.join(result.changed_files)}")
        return ToolResult(output=result.stderr, is_error=True)
