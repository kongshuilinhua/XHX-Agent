from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from xhx_agent.evidence.store import EvidenceStore
from xhx_agent.repo_intel.scanner import ProjectScan
from xhx_agent.runtime.events import EventCallback
from xhx_agent.runtime.profiles import ModelProfile
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.safety.policy import PolicyDecision
from xhx_agent.tools.registry import ToolContext

if TYPE_CHECKING:
    from xhx_agent.runtime.app import RunResult, RuntimeApp

ConfirmationCallback = Callable[[str, PolicyDecision], bool]
CancelCheck = Callable[[], bool]


@dataclass
class OrchestratorContext:
    """Shared base handles + run parameters handed to any orchestrator.

    ``RuntimeApp.run_task`` builds the worktree / evidence / kernel / scan /
    tool_context, wraps them here, and passes this to the selected
    orchestrator's ``run()``. Orchestrators read everything they need from this
    context and never construct the base themselves.
    """

    app: RuntimeApp
    task: str
    run_id: str
    workspace: Path
    profile: ModelProfile
    scan: ProjectScan
    evidence: EvidenceStore
    kernel: SafeExecutionKernel
    tool_context: ToolContext
    assume_yes: bool = False
    confirm_callback: ConfirmationCallback | None = None
    auto_repair: bool = False
    cancel_check: CancelCheck | None = None
    event_callback: EventCallback | None = None
    metrics_tracker: dict[str, int] = field(default_factory=lambda: {"tokens": 0})


class Orchestrator(Protocol):
    """A top-level control-flow strategy over the shared base.

    Implementations decide *how* a task is driven (single autonomous loop vs.
    multi-agent graph) while reusing the same tools, safety kernel, context
    compiler and evidence store via :class:`OrchestratorContext`.
    """

    name: str

    def run(self, ctx: OrchestratorContext) -> RunResult: ...
