"""目录型 Skill：通过 tool.json + references/*.py 定义自定义工具。"""

from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xhx_agent.tools.registry import ToolContext, ToolDefinition, ToolExecutionResult, ToolRunner

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# tool.json 解析
# ---------------------------------------------------------------------------


def parse_tool_json(path: Path) -> list[dict[str, Any]]:
    """解析 tool.json 文件，返回工具 schema 列表。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        log.warning("Failed to parse tool.json at %s: %s", path, e)
        return []

    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        log.warning("tool.json at %s must be a JSON array or object", path)
        return []

    return raw


def load_tool_implementation(references_dir: Path, tool_name: str) -> Callable[..., Any] | None:
    """从 references/{tool_name}.py 动态加载 execute 函数。"""
    script = references_dir / f"{tool_name}.py"
    if not script.is_file():
        return None

    module_name = f"xhx_skill_tool_{tool_name}"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        log.warning("Cannot create module spec for %s", script)
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        log.warning("Failed to load tool implementation %s: %s", script, e)
        return None

    execute_fn = getattr(module, "execute", None)
    if execute_fn is None:
        log.warning("Tool implementation %s has no 'execute' function", script)
        return None

    return execute_fn


def register_skill_tools(skill_dir: Path, registry: Any) -> int:
    """从 skill 目录注册自定义工具到 ToolRegistry。

    Returns:
        成功注册的工具数量。
    """
    tool_json_path = skill_dir / "tool.json"
    if not tool_json_path.is_file():
        return 0

    schemas = parse_tool_json(tool_json_path)
    references_dir = skill_dir / "references"
    count = 0

    for schema in schemas:
        tool_name = schema.get("name", "")
        if not tool_name:
            log.warning("Skipping tool with no name in %s", tool_json_path)
            continue

        # 检查是否已注册
        existing = None
        try:
            existing = registry.definition(tool_name)  # type: ignore[union-attr]
        except Exception:
            pass
        if existing is not None:
            log.debug("Tool '%s' already registered, skipping", tool_name)
            continue

        description = schema.get("description", "")
        impl = load_tool_implementation(references_dir, tool_name) if references_dir.is_dir() else None

        if impl is None:
            log.warning("No implementation for tool '%s' in %s", tool_name, references_dir)

        # 注册为 custom_ 前缀工具（利用 XHX-Agent 的约定）
        params = schema.get("parameters", schema.get("input_schema", {}))
        try:
            definition = ToolDefinition(
                name=f"custom_{tool_name}",
                description=description,
                parameters=params,
                read_only=False,
                destructive=False,
                network=False,
                is_command=False,
                runner=_make_skill_runner(tool_name, impl),
            )
            registry.register_definition(definition)  # type: ignore[union-attr]
        except Exception as e:
            log.warning("Failed to register skill tool '%s': %s", tool_name, e)
            continue

        count += 1

    return count


def _make_skill_runner(tool_name: str, impl: Callable[..., Any] | None) -> ToolRunner:
    """工厂：为单个 skill 工具生成 runner，避免在循环里用 lambda 误捕获循环变量。"""

    def runner(_ctx: ToolContext, args: dict[str, Any]) -> ToolExecutionResult:
        return _run_skill_tool(tool_name, impl, args)

    return runner


def _run_skill_tool(tool_name: str, impl: Callable[..., Any] | None, args: dict[str, Any]) -> ToolExecutionResult:
    """运行 Skill 自定义工具，返回标准 ToolExecutionResult（与其他工具 runner 一致）。"""
    tool = f"custom_{tool_name}"
    if impl is None:
        msg = f"Error: no implementation found for tool '{tool_name}'"
        return ToolExecutionResult(
            tool=tool,
            status="failed",
            summary=msg,
            trace_payload={"tool": tool, "error": msg},
            error=msg,
        )
    try:
        text = str(impl(**args))
        return ToolExecutionResult(
            tool=tool,
            status="success",
            summary=text,
            trace_payload={"tool": tool, "arguments": args, "content": text},
            evidence_kind="file",
            evidence_source=tool,
            evidence_summary=text,
        )
    except Exception as e:
        msg = f"Tool execution error: {e}"
        return ToolExecutionResult(
            tool=tool,
            status="failed",
            summary=msg,
            trace_payload={"tool": tool, "error": str(e)},
            error=str(e),
        )
