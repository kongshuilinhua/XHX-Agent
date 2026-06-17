"""Agent 工具过滤：按定义限制工具集。

来源：mewcode agents/tool_filter.py，适配 XHX-Agent 的 ToolRegistry。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xhx_agent.agents.parser import AgentDef
    from xhx_agent.tools.registry import ToolRegistry

# 所有子 agent 禁用的工具
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "dispatch",        # 子 agent 不能再派发
    "present_plan",    # plan 模式专用
})

# 自定义 agent（项目/用户级）额外禁用
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset({
    "dispatch",
    "present_plan",
})


def resolve_agent_tools(
    parent_registry: "ToolRegistry",
    definition: "AgentDef",
    is_background: bool = False,
) -> "list[dict[str, Any]]":
    """根据 AgentDef 过滤工具集，返回 schema 列表。

    过滤层级：
        1. 全局黑名单（ALL_AGENT_DISALLOWED_TOOLS）
        2. 自定义 agent 额外黑名单
        3. 定义中的 disallowed_tools
        4. 定义中的 tools（白名单）

    Returns:
        过滤后的 OpenAI function schema 列表（可直接喂给模型的 tools 参数）。
    """
    # 从 tool_schemas() 收集所有工具名（含 terminal/verify/dispatch 等无 runner 工具）
    all_schemas = parent_registry.tool_schemas()
    all_names = {s["function"]["name"] for s in all_schemas}

    # Layer 1: 全局禁用
    for name in ALL_AGENT_DISALLOWED_TOOLS:
        all_names.discard(name)

    # Layer 2: 自定义 agent 额外限制
    if definition.source in ("project", "user"):
        for name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            all_names.discard(name)

    # Layer 3: 定义中的 disallowed_tools
    if definition.disallowed_tools:
        for name in definition.disallowed_tools:
            all_names.discard(name)

    # Layer 4: 定义中的 tools（白名单）
    if definition.tools:
        allowed_set = set(definition.tools)
        all_names = all_names & allowed_set

    # 过滤 schema
    return [
        s for s in all_schemas
        if s["function"]["name"] in all_names
    ]
