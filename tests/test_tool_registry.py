from __future__ import annotations

from pathlib import Path

import pytest

from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.tools.registry import TOOL_DEFINITIONS, ToolContext, default_tool_registry


def test_tool_registry_rejects_unsupported_tool() -> None:
    plan = ModelPlan(
        summary="bad plan",
        steps=[ToolStep(tool="terminal", arguments={"command": "python -m pytest"})],
    )
    registry = default_tool_registry()

    with pytest.raises(ModelClientError) as exc:
        registry.validate_plan(plan)

    assert exc.value.code == "unsupported_tool"
    assert "terminal" in exc.value.message


def test_tool_registry_rejects_invalid_read_file_arguments() -> None:
    plan = ModelPlan(summary="bad args", steps=[ToolStep(tool="read_file", arguments={})])
    registry = default_tool_registry()

    with pytest.raises(ModelClientError) as exc:
        registry.validate_plan(plan)

    assert exc.value.code == "invalid_tool_arguments"
    assert "read_file" in exc.value.message


def test_tool_registry_executes_apply_patch(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    registry = default_tool_registry()
    step = ToolStep(
        tool="apply_patch",
        arguments={
            "patch": """*** Begin Patch
*** Update File: demo.py
@@
-value = 1
+value = 2
*** End Patch
"""
        },
    )

    registry.validate_plan(ModelPlan(summary="patch", steps=[step]))
    result = registry.execute(ToolContext(workspace=tmp_path), step)

    assert result.status == "success"
    assert result.changed_files == ["demo.py"]
    assert "value = 2" in (tmp_path / "demo.py").read_text(encoding="utf-8")


def test_tool_registry_apply_patch_returns_structured_failure(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    registry = default_tool_registry()
    step = ToolStep(
        tool="apply_patch",
        arguments={
            "patch": """*** Begin Patch
*** Update File: demo.py
@@
-missing = 1
+value = 2
*** End Patch
"""
        },
    )

    registry.validate_plan(ModelPlan(summary="bad patch", steps=[step]))
    result = registry.execute(ToolContext(workspace=tmp_path), step)

    assert result.status == "failed"
    assert result.changed_files == []
    assert result.error
    assert result.evidence_kind is None
    assert (tmp_path / "demo.py").read_text(encoding="utf-8") == "value = 1\n"


def test_definitions_carry_runner():
    # structured (non-command) tools must carry a runner; command tools (terminal/verify) do not
    assert all(d.runner is not None for d in TOOL_DEFINITIONS.values() if not d.is_command)
    assert all(d.runner is None for d in TOOL_DEFINITIONS.values() if d.is_command)


def test_registry_definition_lookup():
    reg = default_tool_registry()
    assert reg.definition("read_file").read_only is True
    assert reg.definition("apply_patch").destructive is True
    assert reg.definition("nope") is None


def test_schema_validation_missing_required():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="read_file", arguments={})])
    with pytest.raises(ModelClientError) as ei:
        reg.validate_plan(plan)
    assert ei.value.code == "invalid_tool_arguments"


def test_schema_validation_wrong_type():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="search", arguments={"query": 123})])
    with pytest.raises(ModelClientError):
        reg.validate_plan(plan)


def test_schema_validation_ok():
    reg = default_tool_registry()
    plan = ModelPlan(summary="s", status="continue", steps=[ToolStep(tool="read_file", arguments={"path": "a.py"})])
    reg.validate_plan(plan)
