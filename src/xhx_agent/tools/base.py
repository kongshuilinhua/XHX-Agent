"""Tool 抽象基类和流式事件类型。

来源：mewcode tools/base.py，与 XHX-Agent 的 ToolDefinition dataclass 互补：
- ToolDefinition → 轻量数据类（schema 唯一来源，XHX 原生方式）
- Tool(ABC)    → 多态基类（复杂工具实现，mewcode 方式）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Tool 基类
# ---------------------------------------------------------------------------

ToolCategory = Literal["read", "write", "command"]


class Tool(ABC):
    """Tool 抽象基类：每种工具实现 execute()。

    与 ToolDefinition（frozen dataclass）互补：
    简单工具用 ToolDefinition + runner 闭包就够了；
    复杂工具（有状态、有额外方法）用 Tool 子类。
    """

    name: str = ""
    description: str = ""
    params_model: type[BaseModel] = BaseModel
    category: ToolCategory = "read"
    is_concurrency_safe: bool = False
    is_system_tool: bool = False
    should_defer: bool = False  # 延迟发现：默认隐藏，按需暴露

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult:
        ...

    def get_schema(self, protocol: str = "openai") -> dict[str, Any]:
        """返回 OpenAI function 格式的 schema。"""
        from pydantic import Field

        props: dict[str, Any] = {}
        required: list[str] = []

        for field_name, field_info in self.params_model.model_fields.items():
            if field_name == "model_config":
                continue
            if hasattr(field_info, "annotation"):
                json_type = _pydantic_to_json_type(field_info)
                props[field_name] = json_type
            if field_info.is_required():
                required.append(field_name)

        input_schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            input_schema["required"] = required

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": input_schema,
            },
        }


# ---------------------------------------------------------------------------
# ToolResult
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    """工具执行结果。"""
    output: str
    is_error: bool = False


# ---------------------------------------------------------------------------
# 流式事件类型
# ---------------------------------------------------------------------------


@dataclass
class TextDelta:
    """模型输出的增量文本片段。"""
    text: str


@dataclass
class ToolCallStart:
    """工具调用开始。"""
    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallDelta:
    """工具调用参数增量（流式到达）。"""
    tool_use_id: str
    delta: str


@dataclass
class ToolCallComplete:
    """工具调用完成（参数拼接完毕）。"""
    tool_use_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass
class ThinkingDelta:
    """思考链增量文本。"""
    text: str


@dataclass
class ThinkingComplete:
    """思考链结束。"""
    pass


@dataclass
class StreamEnd:
    """流式响应结束。"""
    pass


StreamEvent = TextDelta | ToolCallStart | ToolCallDelta | ToolCallComplete | ThinkingDelta | ThinkingComplete | StreamEnd


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _pydantic_to_json_type(field_info: Any) -> dict[str, Any]:
    """Pydantic field → JSON Schema type dict（简化版）。"""
    annotation = getattr(field_info, "annotation", None)
    if annotation is None:
        return {"type": "string"}

    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", ())

    type_map = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object"}
    for py_type, json_type in type_map.items():
        if annotation is py_type or origin is py_type:
            result: dict[str, Any] = {"type": json_type}
            if hasattr(field_info, "description") and field_info.description:
                result["description"] = field_info.description
            return result

    return {"type": "string"}
