from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class LinearOrchestrator:
    """linear 范式：auto-classification 的 fallback，首次产生改动即停（stop-on-first-change）。

    与 loop 共用 RuntimeApp._run_linear，但不开 autonomous，保留早期「改一处就停」的行为，
    主要为向后兼容；真正的自主多轮循环是 loop。
    """

    name = "linear"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        return ctx.app._run_linear(ctx)
