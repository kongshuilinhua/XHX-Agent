from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class PlanOrchestrator:
    """plan 范式：自主 plan-execute 循环（原 loop 改名而来，行为不变）。

    复用 RuntimeApp._run_linear，开启 autonomous：模型持续迭代读→改→验证多轮，
    直到自报完成或触达 config.max_loop_turns。tool-calling 迁移在 Phase 3。
    """

    name = "plan"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        ctx.autonomous = True
        return ctx.app._run_linear(ctx)
