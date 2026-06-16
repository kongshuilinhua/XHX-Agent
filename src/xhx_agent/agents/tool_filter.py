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
    parent_registry: ToolRegistry,
    definition: AgentDef,
    is_background: bool = False,
) -> ToolRegistry:
    """根据 AgentDef 过滤工具集，返回新的 ToolRegistry。

    过滤层级：
        1. 全局黑名单（ALL_AGENT_DISALLOWED_TOOLS）
        2. 自定义 agent 额外黑名单
        3. 定义中的 disallowed_tools
        4. 定义中的 tools（白名单）
    """
    # 先收集所有工具名
    all_names = set(parent_registry.tool_schemas_names())

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

    # 构建新的 tool schemas 列表
    filtered_schemas = [
        s for s in parent_registry.tool_schemas()
        if s.get("name", "") in all_names
    ]
    return filtered_schemas  # type: ignore[return-value]
