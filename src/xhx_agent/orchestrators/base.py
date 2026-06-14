"""可插拔编排器的抽象底座：定义 Orchestrator 协议与传给它的 OrchestratorContext。

双范式（loop / graph / linear / dag）都实现同一个 Orchestrator.run(ctx)，共用 ctx 里的
工具 / 安全内核 / 上下文 / 证据等基座——只有顶层控制流不同。编排器从不自己构造基座，全部从 ctx 取。
"""

from __future__ import annotations

import threading
import time
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

# 当 git worktree 隔离不可用（非 git 仓库，或建 worktree 失败）而直接在用户工作区执行时抛出。
# 这种模式下失败的运行会把文件改动留在原地，没有自动的基线回滚。
IN_PLACE_WARNING = (
    "No git worktree isolation: changes were applied directly to the workspace and are NOT "
    "automatically rolled back on failure. Review the diff manually, or run inside a git "
    "repository for isolated execution."
)


@dataclass
class OrchestratorContext:
    """传给任意编排器的共享基座句柄 + 运行参数。

    RuntimeApp.run_task 先建好 worktree / evidence / kernel / scan / tool_context，
    打包进这里，再交给所选编排器的 run()。编排器从本 ctx 读取所需的一切，绝不自己构造基座。
    """

    app: RuntimeApp
    task: str
    run_id: str
    workspace: Path
    original_workspace: Path
    profile: ModelProfile
    scan: ProjectScan
    evidence: EvidenceStore
    kernel: SafeExecutionKernel
    tool_context: ToolContext
    start_time: float = field(default_factory=time.time)
    isolated: bool = True
    mode: str = ""
    autonomous: bool = False
    assume_yes: bool = False
    confirm_callback: ConfirmationCallback | None = None
    auto_repair: bool = False
    cancel_check: CancelCheck | None = None
    event_callback: EventCallback | None = None
    metrics_tracker: dict[str, int] = field(default_factory=lambda: {"tokens": 0})
    prior_messages: list[dict] | None = None
    # 写型子 agent 串行合并用：rel_path → 最先改它的子 agent 标签；用于跨子 agent 的冲突检测（先到先得）。
    subagent_claims: dict[str, str] = field(default_factory=dict)
    # 并行写子 agent 用：串行化 git worktree 创建/清理 + _merge_into_parent（claims 与拷贝）的临界区。
    subagent_lock: threading.Lock = field(default_factory=threading.Lock)


class Orchestrator(Protocol):
    """基于共享基座的顶层控制流策略。

    各实现决定一个任务「怎么」被驱动（单一自主 loop vs 多 agent graph），
    同时通过 OrchestratorContext 复用同一套工具、安全内核、上下文编译器和证据存储。
    """

    name: str

    def run(self, ctx: OrchestratorContext) -> RunResult: ...
