"""运行时共享类型：PlanReview、IN_PLACE_WARNING 等编排无关的基础类型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class PlanReview:
    decision: Literal["execute", "revise", "cancel"]
    feedback: str | None = None


# 当 git worktree 隔离不可用（非 git 仓库，或建 worktree 失败）而直接在用户工作区执行时抛出。
# 这种模式下失败的运行会把文件改动留在原地，没有自动的基线回滚。
IN_PLACE_WARNING = (
    "No git worktree isolation: changes were applied directly to the workspace and are NOT "
    "automatically rolled back on failure. Review the diff manually, or run inside a git "
    "repository for isolated execution."
)
