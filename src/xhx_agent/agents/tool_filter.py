"""Agent 工具过滤：按定义限制工具集。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xhx_agent.agents.parser import AgentDef
    from xhx_agent.tools import ToolRegistry

# 所有子 agent 禁用的工具
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "dispatch",  # 子 agent 不能再派发
        "Agent",  # 同上：子 agent 不能再 spawn 子 agent
        "present_plan",  # plan 模式专用
        "ExitPlanMode",  # 进出 plan 模式仅限顶层 agent
        "EnterPlanMode",
    }
)

# 自定义 agent（项目/用户级）额外禁用
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "dispatch",
        "Agent",
        "present_plan",
        "ExitPlanMode",
        "EnterPlanMode",
    }
)


def resolve_agent_tools(
    parent_registry: ToolRegistry,
    definition: AgentDef,
    is_background: bool = False,
) -> list[dict[str, Any]]:
    """根据 AgentDef 过滤工具集，返回 schema 列表。

    过滤层级：
        1. 全局黑名单（ALL_AGENT_DISALLOWED_TOOLS）
        2. 自定义 agent 额外黑名单
        3. 定义中的 disallowed_tools
        4. 定义中的 tools（白名单）

    Returns:
        过滤后的 OpenAI function schema 列表（可直接喂给模型的 tools 参数）。
    """
    # 从 tool_schemas() / get_all_schemas() 收集所有工具名（兼容两套 registry）
    all_schemas = _get_schemas(parent_registry)
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
    return [s for s in all_schemas if s["function"]["name"] in all_names]


def _get_schemas(registry: Any) -> list[dict[str, Any]]:
    """从 Tool 式 registry 获取 schema 列表。"""
    if hasattr(registry, "get_all_schemas"):
        return registry.get_all_schemas("openai-compat")
    if hasattr(registry, "tool_schemas"):
        return registry.tool_schemas()
    return []


def build_filtered_registry(parent_registry: Any, definition: Any, is_background: bool = False) -> Any:
    """按 AgentDef 过滤后，拷贝命中工具的 Tool 实例到一个新的 Tool 式 ``ToolRegistry``。

    子 agent 需要的是可执行的 registry（`get(name)` → Tool），而非 `resolve_agent_tools`
    返回的 schema 列表。两条子 agent 创建路径与 ``build_teammate_tools`` 共用此函数。
    """
    from xhx_agent.tools import ToolRegistry

    filtered_names = {s["function"]["name"] for s in resolve_agent_tools(parent_registry, definition, is_background)}
    registry = ToolRegistry()
    for tool in parent_registry.list_tools():
        if tool.name in filtered_names:
            registry.register(tool)
    return registry


def build_teammate_tools(
    parent_registry: Any,
    team_manager: Any,
    team_name: str,
    agent_id: str,
    agent_name: str,
    backend_type: str,
    definition: Any,
) -> Any:
    """构建队友的完整工具集（Tool 式 registry）。

    基于 ``resolve_agent_tools`` 的过滤结果，从 parent_registry 中拷贝命中
    的 Tool 实例到新 ToolRegistry，然后追加队友专属工具（TaskCreateTool /
    TaskUpdateTool / SendMessageTool 真实实例）。返回 Tool 式 ``ToolRegistry``。
    """
    from xhx_agent.tools.send_message import SendMessageTool
    from xhx_agent.tools.task_create import TaskCreateTool
    from xhx_agent.tools.task_update import TaskUpdateTool

    # 1+2) 过滤并拷贝命中工具的 Tool 实例到新的 Tool 式 registry
    registry = build_filtered_registry(parent_registry, definition, False)

    # 3) 注册真实队友专属工具实例（不再用 stub）
    registry.register(TaskCreateTool(team_manager, team_name, agent_name))
    registry.register(TaskUpdateTool(team_manager, team_name))
    registry.register(SendMessageTool(team_manager, team_name, agent_name))

    return registry
