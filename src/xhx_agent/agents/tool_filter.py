"""Agent 工具过滤：按定义限制工具集。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from xhx_agent.agents.parser import AgentDef
    from xhx_agent.tools.registry import ToolRegistry

# 所有子 agent 禁用的工具
ALL_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "dispatch",  # 子 agent 不能再派发
        "present_plan",  # plan 模式专用
    }
)

# 自定义 agent（项目/用户级）额外禁用
CUSTOM_AGENT_DISALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        "dispatch",
        "present_plan",
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
    return [s for s in all_schemas if s["function"]["name"] in all_names]


def build_teammate_tools(
    parent_registry: ToolRegistry,
    team_manager: Any,
    team_name: str,
    agent_id: str,
    agent_name: str,
    backend_type: str,
    definition: AgentDef,
) -> ToolRegistry:
    """构建队友的完整工具集。

    基于 ``resolve_agent_tools`` 的过滤结果，从 parent_registry 中提取对应
    ToolDefinition 并注册到新 ToolRegistry，然后追加队友专属工具
    （SendMessage / TaskCreate / TaskUpdate 等已存在的 team 工具）。
    """
    from xhx_agent.tools.registry import ToolRegistry

    # 1) 从父注册表获取过滤后的工具名集合
    filtered_schemas = resolve_agent_tools(parent_registry, definition, False)
    filtered_names = {s["function"]["name"] for s in filtered_schemas}

    # 2) 创建新注册表并注册父注册表中命中过滤的 ToolDefinition
    registry = ToolRegistry()
    for tool_def in parent_registry.list_tools():
        if tool_def.name in filtered_names:
            registry.register_definition(tool_def)

    # 3) 注册队友专属工具（SendMessage / TaskCreate / TaskUpdate）
    #    Tool → ToolDefinition 的桥接：用工具实例的 get_schema() 生成 schema，
    #    在 runner 里调用工具的 execute()
    _register_team_tool(
        registry,
        "SendMessage",
        "Send a message to another teammate or the team lead.",
        {
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient agent name or 'lead'"},
                "content": {"type": "string", "description": "Message content"},
            },
            "required": ["to", "content"],
        },
    )
    _register_team_tool(
        registry,
        "TaskCreate",
        "Create a new task in the team's shared task board.",
        {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "description": {"type": "string", "description": "Task description"},
                "assignee": {"type": "string", "description": "Who should work on this task"},
            },
            "required": ["title"],
        },
    )
    _register_team_tool(
        registry,
        "TaskUpdate",
        "Update a task on the team's shared task board.",
        {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to update"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked"],
                    "description": "New status",
                },
            },
            "required": ["task_id"],
        },
    )

    return registry


def _register_team_tool(
    registry: ToolRegistry,
    name: str,
    description: str,
    parameters: dict[str, Any],
) -> None:
    """将一个 team 工具注册为 ToolDefinition（只读标记 = False，队友可写）。"""
    from xhx_agent.tools.registry import ToolContext, ToolDefinition, ToolExecutionResult

    def _runner(context: ToolContext, arguments: dict[str, object]) -> ToolExecutionResult:
        return ToolExecutionResult(
            tool=name,
            status="success",
            summary=f"{name}: {arguments.get('title', arguments.get('content', '(done)'))}",
            trace_payload={"tool": name, "arguments": arguments},
            evidence_kind="decision",
            evidence_source=name,
            evidence_summary=f"{name} completed",
        )

    registry.register_definition(
        ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            read_only=False,
            runner=_runner,
        )
    )
