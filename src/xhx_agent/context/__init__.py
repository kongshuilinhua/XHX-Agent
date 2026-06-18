"""上下文系统：编译 + 压缩 + 内容替换。"""

from xhx_agent.context.compiler import compile_context_pack
from xhx_agent.context.manager import (
    CompactBoundary,
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementRecord,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    clone_replacement_state,
    create_replacement_state,
    ensure_session_dir,
    load_replacement_records,
    persist_tool_result,
    reconstruct_replacement_state,
)
from xhx_agent.context.pack import ContextItem, ContextPack

__all__ = [
    "CompactBoundary",
    "CompactCircuitBreaker",
    "CompactEvent",
    "ContentReplacementRecord",
    "ContentReplacementState",
    "ContextItem",
    "ContextPack",
    "RecoveryState",
    "append_replacement_records",
    "apply_tool_result_budget",
    "auto_compact",
    "clone_replacement_state",
    "compile_context_pack",
    "create_replacement_state",
    "ensure_session_dir",
    "load_replacement_records",
    "persist_tool_result",
    "reconstruct_replacement_state",
]
