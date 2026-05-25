from __future__ import annotations

from pathlib import Path

from xhx_agent.models.types import MockPlan, ToolStep


class MockModelClient:
    """Deterministic v0.1 planner for fixtures and local smoke tests."""

    def plan(self, task: str, workspace: Path) -> MockPlan:
        lower_task = task.lower()
        if self._looks_like_python_fixture(workspace, lower_task):
            return MockPlan(
                summary="Mock plan for Python failing test fixture.",
                steps=[
                    ToolStep(tool="search", arguments={"query": "TODO_BUG", "glob": "*.py"}),
                    ToolStep(tool="read_file", arguments={"path": "src/calc.py"}),
                    ToolStep(
                        tool="apply_patch",
                        arguments={
                            "patch": """*** Begin Patch
*** Update File: src/calc.py
@@
-    return a - b  # TODO_BUG
+    return a + b
*** End Patch
"""
                        },
                    ),
                ],
            )
        if self._looks_like_node_fixture(workspace, lower_task):
            return MockPlan(
                summary="Mock plan for Node failing test fixture.",
                steps=[
                    ToolStep(tool="search", arguments={"query": "TODO_BUG", "glob": "*.js"}),
                    ToolStep(tool="read_file", arguments={"path": "src/index.js"}),
                    ToolStep(
                        tool="apply_patch",
                        arguments={
                            "patch": """*** Begin Patch
*** Update File: src/index.js
@@
-  return a - b; // TODO_BUG
+  return a + b;
*** End Patch
"""
                        },
                    ),
                ],
            )
        return MockPlan(
            summary="Read-only mock plan.",
            steps=[
                ToolStep(tool="search", arguments={"query": task.split()[0] if task.split() else "README", "glob": "*"}),
            ],
        )

    def _looks_like_python_fixture(self, workspace: Path, task: str) -> bool:
        return (workspace / "src" / "calc.py").exists() and ("fix" in task or "修复" in task or "failing" in task)

    def _looks_like_node_fixture(self, workspace: Path, task: str) -> bool:
        return (workspace / "src" / "index.js").exists() and ("fix" in task or "修复" in task or "failing" in task)
