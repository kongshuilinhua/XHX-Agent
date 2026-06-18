__version__ = "1.0.0"

from xhx_agent.cli.rpc import start_rpc_loop
from xhx_agent.evals.benchmark import BenchmarkRunner
from xhx_agent.evals.replay import TrailReplayer
from xhx_agent.evidence.store import EvidenceEntry, EvidenceStore, RawTraceEntry
from xhx_agent.hooks import HooksManager, hooks_manager
from xhx_agent.runtime.result import RunResult
from xhx_agent.safety.kernel import SafeExecutionKernel
from xhx_agent.skills.loader import SkillLoader
from xhx_agent.skills.mcp import MCPManager
from xhx_agent.tools.registry import ToolContext, ToolExecutionResult, ToolRegistry

__all__ = [
    "RunResult",
    "ToolRegistry",
    "ToolContext",
    "ToolExecutionResult",
    "EvidenceStore",
    "EvidenceEntry",
    "RawTraceEntry",
    "SafeExecutionKernel",
    "SkillLoader",
    "HooksManager",
    "hooks_manager",
    "MCPManager",
    "TrailReplayer",
    "BenchmarkRunner",
    "start_rpc_loop",
]
