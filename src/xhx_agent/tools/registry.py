from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.skills.hooks import hooks_manager
from xhx_agent.tools.patch import PatchResult, apply_patch
from xhx_agent.tools.read_file import read_file
from xhx_agent.tools.search import search

ToolName = Literal["search", "read_file", "apply_patch"]


class ToolExecutionResult(BaseModel):
    tool: str
    status: str
    summary: str
    trace_payload: dict[str, Any]
    evidence_kind: str | None = None
    evidence_source: str | None = None
    evidence_summary: str | None = None
    changed_files: list[str] = []
    error: str | None = None


class ToolContext(BaseModel):
    workspace: Path
    max_file_bytes: int = 200_000

    model_config = {"arbitrary_types_allowed": True}


ToolRunner = Callable[[ToolContext, dict[str, Any]], ToolExecutionResult]


def _run_search(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    query = str(arguments["query"])
    glob = arguments.get("glob")
    results = search(
        context.workspace,
        query,
        glob=str(glob) if glob else None,
        max_results=int(arguments.get("max_results", 50)),
    )
    return ToolExecutionResult(
        tool="search",
        status="success",
        summary=f"search returned {len(results)} result(s)",
        trace_payload={"tool": "search", "query": query, "result_count": len(results), "results": results},
        evidence_kind="file",
        evidence_source="search",
        evidence_summary=f"search returned {len(results)} result(s)",
    )


def _run_read_file(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    path = str(arguments["path"])
    start_line = int(arguments.get("start_line", 1))
    max_lines = int(arguments.get("max_lines", 200))
    content = read_file(
        context.workspace,
        path,
        max_bytes=context.max_file_bytes,
        start_line=start_line,
        max_lines=max_lines,
    )
    return ToolExecutionResult(
        tool="read_file",
        status="success",
        summary=f"read {path}",
        trace_payload={"tool": "read_file", "path": path, "content": content},
        evidence_kind="file",
        evidence_source=path,
        evidence_summary=f"read {path}",
    )


def _run_apply_patch(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    with contextlib.suppress(Exception):
        hooks_manager.trigger("before_patch", workspace=context.workspace, patch=str(arguments.get("patch", "")))
    result: PatchResult = apply_patch(context.workspace, str(arguments["patch"]))

    return ToolExecutionResult(
        tool="apply_patch",
        status=result.status,
        summary=f"changed files: {', '.join(result.changed_files)}" if result.status == "success" else result.stderr,
        trace_payload={"tool": "apply_patch", **result.model_dump()},
        evidence_kind="patch" if result.status == "success" else None,
        evidence_source="apply_patch" if result.status == "success" else None,
        evidence_summary=f"changed files: {', '.join(result.changed_files)}" if result.status == "success" else None,
        changed_files=result.changed_files,
        error=result.stderr if result.status != "success" else None,
    )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    read_only: bool = False
    destructive: bool = False
    is_command: bool = False
    runner: ToolRunner | None = None


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "search": ToolDefinition(
        name="search", description="在仓库内按文本搜索，返回匹配的文件/行。只读。",
        parameters={"type": "object", "properties": {
            "query": {"type": "string", "description": "搜索文本"},
            "glob": {"type": "string", "description": "可选文件名 glob，如 *.py"},
            "max_results": {"type": "integer", "default": 50}},
            "required": ["query"]},
        read_only=True, runner=_run_search),
    "read_file": ToolDefinition(
        name="read_file", description="按行读取仓库内文件内容。只读。",
        parameters={"type": "object", "properties": {
            "path": {"type": "string", "description": "相对路径"},
            "start_line": {"type": "integer", "default": 1},
            "max_lines": {"type": "integer", "default": 200}},
            "required": ["path"]},
        read_only=True, runner=_run_read_file),
    "apply_patch": ToolDefinition(
        name="apply_patch", description="用 *** Begin Patch/*** End Patch 格式对文件做增量修改。会改文件。",
        parameters={"type": "object", "properties": {
            "patch": {"type": "string", "description": "完整 patch 文本"}},
            "required": ["patch"]},
        destructive=True, runner=_run_apply_patch),
    "terminal": ToolDefinition(
        name="terminal",
        description=("在仓库工作区运行一条 shell 命令并返回输出。命令会过安全风险分级："
                     "只读命令(ls/cat/git status 等)自动执行；测试等命令需用户确认；"
                     "危险命令(rm/curl/bash/sudo/重定向等)被拒。"),
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "要执行的完整命令（单条，不要用 ; | & 等拼接）"}},
            "required": ["command"]},
        is_command=True),
    "verify": ToolDefinition(
        name="verify",
        description="运行项目测试做验证。可选 command（默认按项目语言推断，如 python -m pytest）。",
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "可选：自定义验证命令；省略则用项目默认测试命令"}},
            "required": []},
        is_command=True),
}


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRunner] = {}
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, name: ToolName, runner: ToolRunner) -> None:
        self._tools[name] = runner

    def register_definition(self, d: ToolDefinition) -> None:
        self._definitions[d.name] = d
        if d.runner is not None:
            self._tools[d.name] = d.runner

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """导出已注册工具的 OpenAI function 格式 schema（喂给模型的 tools 参数）。"""
        return [
            {"type": "function", "function": {
                "name": d.name, "description": d.description, "parameters": d.parameters}}
            for d in self._definitions.values()
        ]

    def validate_plan(self, plan: ModelPlan) -> None:
        for index, step in enumerate(plan.steps, start=1):
            if step.tool not in self._tools:
                raise ModelClientError(
                    code="unsupported_tool",
                    message=f"Model plan step {index} requested unsupported tool: {step.tool}",
                    details={"tool": step.tool, "step": step.model_dump()},
                )
            d = self._definitions.get(step.tool)
            if d is not None:
                _validate_against_schema(index, step, d.parameters)

    def execute(self, context: ToolContext, step: ToolStep) -> ToolExecutionResult:
        if step.tool not in self._tools:
            return ToolExecutionResult(
                tool=step.tool,
                status="failed",
                summary=f"Unsupported tool: {step.tool}",
                trace_payload={"tool": step.tool, "error": "unsupported tool"},
                error=f"Unsupported tool: {step.tool}",
            )
        return self._tools[step.tool](context, step.arguments)


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for d in TOOL_DEFINITIONS.values():
        registry.register_definition(d)
    return registry


def _invalid_tool_arguments(index: int, step: ToolStep, message: str) -> ModelClientError:
    return ModelClientError(
        code="invalid_tool_arguments",
        message=f"Model plan step {index} is invalid: {message}",
        details={"tool": step.tool, "step": step.model_dump()},
    )


_JSON_PY_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str, "integer": int, "number": (int, float),
    "boolean": bool, "object": dict, "array": list,
}


def _validate_against_schema(index: int, step: ToolStep, schema: dict[str, Any]) -> None:
    props = schema.get("properties", {})
    required = schema.get("required", [])
    args = step.arguments
    for key in required:
        val = args.get(key)
        if val is None or (isinstance(val, str) and not val):
            raise _invalid_tool_arguments(index, step, f"{step.tool} requires non-empty argument: {key}")
    for key, val in args.items():
        spec = props.get(key)
        if not spec or val is None:
            continue
        py = _JSON_PY_TYPES.get(spec.get("type", ""))
        if py and not isinstance(val, py):
            raise _invalid_tool_arguments(index, step, f"{step.tool} argument {key} must be {spec['type']}")
