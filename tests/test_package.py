from __future__ import annotations

import xhx_agent


def test_package_exports() -> None:
    # Verify version
    assert xhx_agent.__version__ == "1.0.0"

    # Verify key exports are accessible and not None
    assert xhx_agent.RunResult is not None
    assert xhx_agent.ToolRegistry is not None
    assert xhx_agent.EvidenceStore is not None
    assert xhx_agent.EvidenceEntry is not None
    assert xhx_agent.RawTraceEntry is not None
    assert xhx_agent.SkillLoader is not None
    assert xhx_agent.HooksManager is not None
    assert xhx_agent.hooks_manager is not None
    assert xhx_agent.MCPManager is not None
    assert xhx_agent.TrailReplayer is not None
    assert xhx_agent.BenchmarkRunner is not None
    assert xhx_agent.start_rpc_loop is not None


def test_package_all_attribute() -> None:
    # Verify __all__ list matches exposed names
    expected = [
        "RunResult",
        "ToolRegistry",
        "EvidenceStore",
        "EvidenceEntry",
        "RawTraceEntry",
        "SkillLoader",
        "HooksManager",
        "hooks_manager",
        "MCPManager",
        "TrailReplayer",
        "BenchmarkRunner",
        "start_rpc_loop",
    ]
    assert sorted(xhx_agent.__all__) == sorted(expected)
