from __future__ import annotations

from typing import TYPE_CHECKING

from xhx_agent.orchestrators.base import OrchestratorContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult


class LoopOrchestrator:
    """loop 范式：统一的自主 agent 循环（类 Claude Code），默认主范式。

    复用 RuntimeApp._run_linear，但开启 autonomous：模型持续迭代（读→改→验证）多轮，
    直到自己报告完成或触达 config.max_loop_turns，而非首次改动就停。
    （auto-classification 的 fallback 用 LinearOrchestrator，保留首改即停的旧行为。）
    """

    name = "loop"

    def run(self, ctx: OrchestratorContext) -> RunResult:
        ctx.autonomous = True
        return ctx.app._run_linear(ctx)
