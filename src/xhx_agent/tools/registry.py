from __future__ import annotations

import contextlib
from collections.abc import Callable
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


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolRunner] = {}

    def register(self, name: ToolName, runner: ToolRunner) -> None:
        self._tools[name] = runner

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def validate_plan(self, plan: ModelPlan) -> None:
        for index, step in enumerate(plan.steps, start=1):
            if step.tool not in self._tools:
                raise ModelClientError(
                    code="unsupported_tool",
                    message=f"Model plan step {index} requested unsupported tool: {step.tool}",
                    details={"tool": step.tool, "step": step.model_dump()},
                )
            self._validate_arguments(index, step)

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

    def _validate_arguments(self, index: int, step: ToolStep) -> None:
        if step.tool == "search":
            query = step.arguments.get("query")
            if not isinstance(query, str) or not query:
                raise _invalid_tool_arguments(index, step, "search requires non-empty string argument: query")
            glob = step.arguments.get("glob")
            if glob is not None and not isinstance(glob, str):
                raise _invalid_tool_arguments(index, step, "search argument glob must be a string when provided")
            return
        if step.tool == "read_file":
            path = step.arguments.get("path")
            if not isinstance(path, str) or not path:
                raise _invalid_tool_arguments(index, step, "read_file requires non-empty string argument: path")
            return
        if step.tool == "apply_patch":
            patch = step.arguments.get("patch")
            if not isinstance(patch, str) or not patch:
                raise _invalid_tool_arguments(index, step, "apply_patch requires non-empty string argument: patch")


def default_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("search", _run_search)
    registry.register("read_file", _run_read_file)
    registry.register("apply_patch", _run_apply_patch)
    return registry


def _invalid_tool_arguments(index: int, step: ToolStep, message: str) -> ModelClientError:
    return ModelClientError(
        code="invalid_tool_arguments",
        message=f"Model plan step {index} is invalid: {message}",
        details={"tool": step.tool, "step": step.model_dump()},
    )


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
