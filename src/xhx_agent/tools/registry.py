from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.hooks import hooks_manager
from xhx_agent.tools.patch import PatchResult, apply_patch
from xhx_agent.tools.read_file import read_file
from xhx_agent.tools.search import search

ToolName = Literal["search", "read_file", "apply_patch", "repo_query", "present_plan"]


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
    # 运行时 workspace 会被切到隔离 git worktree，而 worktree 只含被 git 跟踪的文件——
    # gitignored 的 .xhx/ 不在其中。需要读项目级配置/密钥（如 web_search 的 tavily key）的工具
    # 必须用 original_workspace（原始项目根）去 load_config，否则读不到 .xhx/config.json。
    original_workspace: Path | None = None
    max_file_bytes: int = 200_000
    allowed_dirs: list[Path] = Field(default_factory=list)
    permission_mode: str = "default"

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


def _run_repo_query(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    from xhx_agent.repo_intel.index import load_repo_intel_index
    from xhx_agent.repo_intel.references import search_references
    from xhx_agent.repo_intel.symbols import search_symbols

    query = str(arguments["query"])
    kind = str(arguments.get("kind", "symbol"))
    limit = int(arguments.get("limit", 20))

    try:
        index = load_repo_intel_index(context.workspace)
    except Exception as e:
        err_msg = f"Failed to load repository intelligence index: {e}."
        return ToolExecutionResult(
            tool="repo_query",
            status="success",
            summary="Failed to load repository index.",
            trace_payload={
                "tool": "repo_query",
                "query": query,
                "kind": kind,
                "content": err_msg,
            },
            evidence_kind="file",
            evidence_source="repo_query",
            evidence_summary="Failed to load repository index.",
        )

    if kind == "symbol":
        symbols = search_symbols(index.symbol_index, query, limit=limit)
        if not symbols:
            text = "No matching symbols found."
        else:
            text = "\n".join(f"{s.path}:{s.line}  {s.name} ({s.kind})" for s in symbols)
        summary = f"repo_query (symbol) found {len(symbols)} symbol(s)"
    else:  # reference
        references = search_references(index.reference_index, query, limit=limit)
        if not references:
            text = "No matching references found."
        else:
            text = "\n".join(f"{r.path}:{r.line}  {r.name}: {r.excerpt}" for r in references)
        summary = f"repo_query (reference) found {len(references)} reference(s)"

    return ToolExecutionResult(
        tool="repo_query",
        status="success",
        summary=summary,
        trace_payload={
            "tool": "repo_query",
            "query": query,
            "kind": kind,
            "content": text,
        },
        evidence_kind="file",
        evidence_source="repo_query",
        evidence_summary=summary,
    )


def _run_web_fetch(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    url = str(arguments["url"])
    prompt = arguments.get("prompt")
    try:
        from xhx_agent.tools.web import web_fetch

        result_str = web_fetch(url, prompt=prompt, max_bytes=context.max_file_bytes)
        return ToolExecutionResult(
            tool="web_fetch",
            status="success",
            summary=f"Successfully fetched {url}",
            trace_payload={"tool": "web_fetch", "url": url, "result_length": len(result_str), "content": result_str},
            evidence_kind="file",
            evidence_source=url,
            evidence_summary=f"Successfully fetched {url}",
        )
    except Exception as e:
        return ToolExecutionResult(
            tool="web_fetch",
            status="failed",
            summary=f"Failed to fetch {url}: {e}",
            trace_payload={"tool": "web_fetch", "url": url, "error": str(e)},
            error=str(e),
        )


def _run_web_search(context: ToolContext, arguments: dict[str, Any]) -> ToolExecutionResult:
    query = str(arguments["query"])
    from xhx_agent.runtime.config import load_config

    # 用 original_workspace 读配置：run 期 workspace 是 worktree，读不到 gitignored 的 .xhx/。
    cfg = load_config(context.original_workspace or context.workspace)
    api_key = cfg.web_search.tavily_api_key
    if not api_key:
        import os

        api_key_env = cfg.web_search.tavily_api_key_env or "TAVILY_API_KEY"
        api_key = os.environ.get(api_key_env, "")

    if not api_key:
        return ToolExecutionResult(
            tool="web_search",
            status="failed",
            summary="未配置 Tavily API key",
            trace_payload={"tool": "web_search", "query": query, "error": "Missing Tavily API key"},
        )

    try:
        from xhx_agent.tools.web import web_search

        results = web_search(query, api_key, max_results=cfg.web_search.max_results)

        summary_lines = []
        for idx, item in enumerate(results, 1):
            summary_lines.append(f"### {idx}. {item.get('title', 'Untitled')}")
            summary_lines.append(f"URL: {item.get('url', 'N/A')}")
            summary_lines.append(f"Snippet: {item.get('content', '')}\n")
        summary_text = "\n".join(summary_lines) or "No results found."

        return ToolExecutionResult(
            tool="web_search",
            status="success",
            summary=summary_text,
            trace_payload={"tool": "web_search", "query": query, "results": results},
            evidence_kind="file",
            evidence_source="web_search",
            evidence_summary=summary_text,
        )
    except Exception as e:
        return ToolExecutionResult(
            tool="web_search",
            status="failed",
            summary=f"Web search failed: {e}",
            trace_payload={"tool": "web_search", "query": query, "error": str(e)},
            error=str(e),
        )


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema
    read_only: bool = False
    destructive: bool = False
    network: bool = False
    is_command: bool = False
    runner: ToolRunner | None = None


TOOL_DEFINITIONS: dict[str, ToolDefinition] = {
    "present_plan": ToolDefinition(
        name="present_plan",
        description="提交最终设计规划给用户进行确认。提交后将进入两段式的执行确认环节。",
        parameters={
            "type": "object",
            "properties": {
                "plan": {"type": "string", "description": "拟定好的技术实现计划描述。"},
                "files_to_change": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "计划要修改的文件路径列表。",
                },
            },
            "required": ["plan"],
        },
        read_only=True,
        runner=lambda ctx, args: ToolExecutionResult(
            tool="present_plan",
            status="success",
            summary="实现计划已成功呈报，等待用户核准...",
            trace_payload={"tool": "present_plan", **args},
        ),
    ),
    "search": ToolDefinition(
        name="search",
        description="在仓库内按文本搜索，返回匹配的文件/行。只读。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索文本"},
                "glob": {"type": "string", "description": "可选文件名 glob，如 *.py"},
                "max_results": {"type": "integer", "default": 50},
            },
            "required": ["query"],
        },
        read_only=True,
        runner=_run_search,
    ),
    "read_file": ToolDefinition(
        name="read_file",
        description="按行读取仓库内文件内容。只读。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对路径"},
                "start_line": {"type": "integer", "default": 1},
                "max_lines": {"type": "integer", "default": 200},
            },
            "required": ["path"],
        },
        read_only=True,
        runner=_run_read_file,
    ),
    "apply_patch": ToolDefinition(
        name="apply_patch",
        description=(
            "对工作区文件进行增量修改或创建新文件。支持标准 unified diff 格式（推荐）或旧版信封格式。\n"
            "修改文件示例：\n"
            "--- a/src/utils.py\n"
            "+++ b/src/utils.py\n"
            "@@ -10,6 +10,6 @@\n"
            " def add(a, b):\n"
            "-    return a - b\n"
            "+    return a + b\n\n"
            "新建文件示例：\n"
            "--- /dev/null\n"
            "+++ b/src/new_file.py\n"
            "@@ -0,0 +1,2 @@\n"
            "+def hello():\n"
            '+    print("hello")'
        ),
        parameters={
            "type": "object",
            "properties": {"patch": {"type": "string", "description": "完整 patch 文本"}},
            "required": ["patch"],
        },
        destructive=True,
        runner=_run_apply_patch,
    ),
    "terminal": ToolDefinition(
        name="terminal",
        description=(
            "在仓库工作区运行一条 shell 命令并返回输出。命令会过安全风险分级："
            "只读命令(ls/cat/git status 等)自动执行；测试等命令需用户确认；"
            "危险命令(rm/curl/bash/sudo/重定向等)被拒。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的完整命令（单条，不要用 ; | & 等拼接）"}
            },
            "required": ["command"],
        },
        is_command=True,
    ),
    "verify": ToolDefinition(
        name="verify",
        description="运行项目测试做验证。可选 command（默认按项目语言推断，如 python -m pytest）。",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "可选：自定义验证命令；省略则用项目默认测试命令"}
            },
            "required": [],
        },
        is_command=True,
    ),
    "dispatch": ToolDefinition(
        name="dispatch",
        description=(
            "把一个【聚焦、需读多个文件的多步调查】委派给隔离子 agent：它有自己的上下文、受限只读工具、"
            "限定轮数，跑完只回浓缩结论，从而不污染你的主上下文。"
            "适合：摸清不熟悉的模块、并行探索多个独立问题。"
            "不适合：读单个已知文件（直接用 read_file 即可）。"
            "agent_type='explore'（只读：search/read_file）做调查；"
            "agent_type='edit'（可写：search/read_file/apply_patch）在隔离 git worktree 里改代码，"
            "改完自动串行合并回工作区并对冲突文件做检测——适合可并行、互不重叠的修改子任务。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "子任务一句话描述（给人看的）"},
                "prompt": {"type": "string", "description": "给子 agent 的完整指令"},
                "agent_type": {"type": "string", "enum": ["explore", "edit"], "default": "explore"},
            },
            "required": ["prompt"],
        },
    ),
    "repo_query": ToolDefinition(
        name="repo_query",
        description="Query symbol definitions or references in the repository index. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The symbol or word query string"},
                "kind": {
                    "type": "string",
                    "enum": ["symbol", "reference"],
                    "default": "symbol",
                    "description": "Whether to query symbol definitions or references",
                },
                "limit": {"type": "integer", "default": 20, "description": "Maximum number of results to return"},
            },
            "required": ["query"],
        },
        read_only=True,
        runner=_run_repo_query,
    ),
    "web_fetch": ToolDefinition(
        name="web_fetch",
        description="Fetch the content of a web page and convert it to Markdown. Read-only.",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "prompt": {"type": "string", "description": "Optional instructions on what to extract from the page"},
            },
            "required": ["url"],
        },
        read_only=False,
        destructive=False,
        network=True,
        runner=_run_web_fetch,
    ),
    "web_search": ToolDefinition(
        name="web_search",
        description="Search the web for query and return search results. Read-only.",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "The search query"}},
            "required": ["query"],
        },
        read_only=False,
        destructive=False,
        network=True,
        runner=_run_web_search,
    ),
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

    def unregister(self, name: str) -> None:
        """移除一个已注册工具（含 schema 定义）。MCP server 关闭时用，避免共享 registry 残留陈旧定义。"""
        self._tools.pop(name, None)
        self._definitions.pop(name, None)

    def definition(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    @property
    def names(self) -> set[str]:
        return set(self._tools)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """导出已注册工具的 OpenAI function 格式 schema（喂给模型的 tools 参数）。"""
        return [
            {"type": "function", "function": {"name": d.name, "description": d.description, "parameters": d.parameters}}
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
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
    "object": dict,
    "array": list,
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
