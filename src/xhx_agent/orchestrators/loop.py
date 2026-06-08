from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class LoopOrchestrator:
    """Unified autonomous agent loop (Claude-Code-style). The default mode.

    Reuses ``RuntimeApp._run_linear`` but in autonomous mode: the model keeps
    iterating (read -> edit -> verify) across many turns until it reports done or
    hits ``config.max_loop_turns``, instead of stopping after the first change.
    ``LinearOrchestrator`` (used by the auto-classification fallback) keeps the
    original stop-on-first-change behaviour for backward compatibility.
    """

    name = "loop"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        ctx.autonomous = True
        return ctx.app._run_linear(ctx)
