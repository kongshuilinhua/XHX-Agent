"""Hook 条件表达式引擎。

来源：mewcode hooks/conditions.py。

支持的操作符：
    ==  精确匹配
    !=  不等于
    =~  正则匹配（/pattern/ 或直接写 pattern）
    ~=  fnmatch 模式匹配

逻辑组合：&& (and) 或 || (or)，不能混用。
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xhx_agent.hooks.models import HookContext


@dataclass
class Condition:
    """单条条件：field operator value。"""
    field: str
    operator: str
    value: str

    def evaluate(self, ctx: HookContext) -> bool:
        field_value = ctx.get_field(self.field)
        if self.operator == "==":
            return field_value == self.value
        if self.operator == "!=":
            return field_value != self.value
        if self.operator == "=~":
            pattern = self.value
            if pattern.startswith("/") and pattern.endswith("/"):
                pattern = pattern[1:-1]
            try:
                return bool(re.search(pattern, field_value))
            except re.error:
                return False
        if self.operator == "~=":
            return fnmatch.fnmatch(field_value, self.value)
        return False


@dataclass
class ConditionGroup:
    """条件组：多个 Condition 按 and/or 组合。"""
    conditions: list[Condition] = field(default_factory=list)
    logic: str = "and"  # "and" | "or"

    def evaluate(self, ctx: HookContext) -> bool:
        if not self.conditions:
            return True
        if self.logic == "and":
            return all(c.evaluate(ctx) for c in self.conditions)
        return any(c.evaluate(ctx) for c in self.conditions)


class ConditionParseError(Exception):
    """条件表达式解析错误。"""
    pass


_OPERATORS = ("==", "!=", "=~", "~=")


def _parse_single(expr: str) -> Condition:
    expr = expr.strip()
    for op in _OPERATORS:
        idx = expr.find(op)
        if idx == -1:
            continue
        field_part = expr[:idx].strip()
        value_part = expr[idx + len(op):].strip()
        if value_part.startswith('"') and value_part.endswith('"'):
            value_part = value_part[1:-1]
        return Condition(field=field_part, operator=op, value=value_part)
    raise ConditionParseError(f"No valid operator found in condition: '{expr}'")


def parse_condition(expr: str) -> ConditionGroup | None:
    """解析条件表达式字符串。

    Examples:
        ``tool == "Bash"``
        ``tool == "Bash" && args.command =~ "rm *"``
        ``event == "pre_tool_use" || event == "post_tool_use"``
    """
    if not expr or not expr.strip():
        return None

    expr = expr.strip()
    has_and = "&&" in expr
    has_or = "||" in expr

    if has_and and has_or:
        raise ConditionParseError(
            "Cannot mix '&&' and '||' in a single condition expression. "
            "Split into separate hooks instead."
        )

    if has_and:
        parts = expr.split("&&")
        logic = "and"
    elif has_or:
        parts = expr.split("||")
        logic = "or"
    else:
        parts = [expr]
        logic = "and"

    conditions = [_parse_single(p) for p in parts]
    return ConditionGroup(conditions=conditions, logic=logic)
