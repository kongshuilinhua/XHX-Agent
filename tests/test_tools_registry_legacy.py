"""tools/registry.py（legacy 定义式 ToolRegistry）单测。"""

from __future__ import annotations

from pathlib import Path

import pytest

from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.tools.registry import (
    ToolContext,
    ToolDefinition,
    ToolExecutionResult,
    ToolRegistry,
    default_tool_registry,
)


def test_default_registry_schemas_and_lookup() -> None:
    reg = default_tool_registry()
    assert reg.names  # 内置工具非空
    schemas = reg.tool_schemas()
    assert schemas and schemas[0]["type"] == "function"
    assert reg.get_all_schemas() == schemas
    defs = reg.list_tools()
    assert all(isinstance(d, ToolDefinition) for d in defs)
    name = defs[0].name
    assert reg.get(name) is not None and reg.definition(name) is not None


def test_enable_disable_unregister() -> None:
    reg = default_tool_registry()
    name = reg.list_tools()[0].name
    assert reg.is_enabled(name) is True
    reg.disable(name)
    assert reg.is_enabled(name) is False
    reg.enable(name)
    assert reg.is_enabled(name) is True
    reg.unregister(name)
    assert reg.definition(name) is None


def test_register_definition_and_execute(tmp_path: Path) -> None:
    reg = ToolRegistry()

    def _runner(ctx: ToolContext, args: dict) -> ToolExecutionResult:
        return ToolExecutionResult(tool="custom", status="ok", summary=f"ran {args}", trace_payload={})

    reg.register_definition(
        ToolDefinition(name="custom", description="d", parameters={"type": "object"}, runner=_runner)
    )
    ctx = ToolContext(workspace=tmp_path)
    res = reg.execute(ctx, ToolStep(tool="custom", arguments={"a": 1}))
    assert res.status == "ok" and "ran" in res.summary


def test_execute_unsupported_tool(tmp_path: Path) -> None:
    reg = ToolRegistry()
    res = reg.execute(ToolContext(workspace=tmp_path), ToolStep(tool="ghost", arguments={}))
    assert res.status == "failed" and "Unsupported" in res.summary


def test_validate_plan_unsupported_raises() -> None:
    reg = default_tool_registry()
    plan = ModelPlan(summary="p", steps=[ToolStep(tool="ghost_tool", arguments={})])
    with pytest.raises(ModelClientError):
        reg.validate_plan(plan)
