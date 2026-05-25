from __future__ import annotations

from pathlib import Path

import pytest

from xhx_agent.models.types import ModelClientError, ModelPlan, ToolStep
from xhx_agent.tools.registry import ToolContext, default_tool_registry


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
