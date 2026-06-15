from __future__ import annotations

from pathlib import Path

from xhx_agent.models.types import ChatResult, MockPlan, ToolCall, ToolStep


class MockModelClient:
    """Deterministic v0.1 planner for fixtures and local smoke tests."""

    def chat(self, messages: list[dict], tools: list[dict]) -> ChatResult:
        has_present_plan_tool = any(t.get("function", {}).get("name") == "present_plan" for t in tools)
        has_present_plan = False
        for m in messages:
            if m.get("role") == "assistant" and "tool_calls" in m:
                for tc in m["tool_calls"]:
                    func = tc.get("function") or {}
                    if func.get("name") == "present_plan":
                        has_present_plan = True
                        break

        if has_present_plan_tool and not has_present_plan:
            return ChatResult(
                content=None,
                tool_calls=[
                    ToolCall(
                        id="mock_plan_call",
                        name="present_plan",
                        arguments={"plan": "Mock plan summary", "files_to_change": []},
                    )
                ],
            )

        has_tool_result = any(m.get("role") == "tool" for m in messages)
        last_user = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        edit_words = ("fix", "修", "改", "加", "patch", "refactor", "重构")
        is_edit = any(w in str(last_user).lower() for w in edit_words)
        if is_edit and not has_tool_result:
            return ChatResult(
                content=None, tool_calls=[ToolCall(id="mock_call_1", name="read_file", arguments={"path": "README.md"})]
            )
        return ChatResult(content="Mock loop reply: 任务已处理（确定性 mock）。", tool_calls=[])

    def summarize(self, text: str) -> str:
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return "No earlier work to summarize."
        return f"Earlier work covered {len(lines)} tool step(s); most recent: '{lines[-1][:60]}'."

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
                ToolStep(
                    tool="search", arguments={"query": task.split()[0] if task.split() else "README", "glob": "*"}
                ),
            ],
        )

    def _looks_like_python_fixture(self, workspace: Path, task: str) -> bool:
        return (workspace / "src" / "calc.py").exists() and ("fix" in task or "修复" in task or "failing" in task)

    def _looks_like_node_fixture(self, workspace: Path, task: str) -> bool:
        return (workspace / "src" / "index.js").exists() and ("fix" in task or "修复" in task or "failing" in task)
