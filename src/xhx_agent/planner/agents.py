from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.models.types import ModelPlan
from xhx_agent.planner.modes import ReviewDecision
from xhx_agent.planner.reviewer import Reviewer
from xhx_agent.tools.terminal import TerminalResult

if TYPE_CHECKING:
    from xhx_agent.context.pack import ContextPack
    from xhx_agent.runtime.app import RuntimeApp
    from xhx_agent.runtime.profiles import ModelProfile


class PlannerAgent:
    def __init__(self, app: RuntimeApp) -> None:
        self.app = app

    def plan(
        self, task: str, profile: ModelProfile, context_pack: ContextPack, event_callback=None, turn: int = 1
    ) -> ModelPlan:
        """Analyze context and formulate high-level execution steps (DAG or ModelPlan)."""
        return self.app._build_plan(
            task,
            profile,
            context_pack,
            event_callback=event_callback,
            turn=turn,
        )


class CoderAgent:
    def __init__(self, app: RuntimeApp) -> None:
        self.app = app

    def execute_turn(
        self,
        task: str,
        profile: ModelProfile,
        scan,
        evidence,
        kernel,
        tool_context,
        changed_files: list[str],
        tool_summaries: list[str],
        evidence_entries,
        plan_summaries: list[str],
        risks: list[str],
        recent_error: str | None,
        turn: int,
        event_callback=None,
        cancel_check=None,
        metrics_tracker=None,
    ) -> tuple[str, int, str | None]:
        """Apply a code modification or execute a single coding turn."""
        return self.app._run_model_tool_loop(
            task=task,
            profile=profile,
            scan=scan,
            evidence=evidence,
            kernel=kernel,
            tool_context=tool_context,
            changed_files=changed_files,
            tool_summaries=tool_summaries,
            evidence_entries=evidence_entries,
            plan_summaries=plan_summaries,
            risks=risks,
            recent_error=recent_error,
            starting_turn=turn,
            max_turns=1,
            event_callback=event_callback,
            cancel_check=cancel_check,
            metrics_tracker=metrics_tracker,
        )


class ReviewerAgent(Reviewer):
    def __init__(self) -> None:
        super().__init__()

    def review(self, task: str, changed_files: list[str], verification_results: list[TerminalResult]) -> ReviewDecision:
        """Assess Quality Gate criteria on the changed files and verification output."""
        return super().review(task, changed_files, verification_results)
