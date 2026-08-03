from __future__ import annotations

import ast
import math
import operator
from typing import Any

from mini_agent.tools.base import ToolContext, ToolFailure, ToolResult, ToolSpec


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPRESSION_LENGTH = 200
_MAX_AST_NODES = 64
_MAX_ABSOLUTE_EXPONENT = 12
_MAX_ABSOLUTE_RESULT = 1e100


def _evaluate(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_ABSOLUTE_EXPONENT:
            raise ToolFailure("CALCULATOR_LIMIT", "指数绝对值不能超过 12。")
        value = _BINARY_OPERATORS[type(node.op)](left, right)
        if isinstance(value, complex) or not math.isfinite(float(value)):
            raise ToolFailure("CALCULATOR_LIMIT", "计算结果不是有限实数。")
        if abs(value) > _MAX_ABSOLUTE_RESULT:
            raise ToolFailure("CALCULATOR_LIMIT", "计算结果过大。")
        return value
    raise ToolFailure("CALCULATOR_UNSAFE_EXPRESSION", "表达式包含不允许的语法。")


async def _calculator(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    del context
    expression = arguments["expression"].strip()
    if len(expression) > _MAX_EXPRESSION_LENGTH:
        raise ToolFailure("CALCULATOR_LIMIT", "表达式过长。")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ToolFailure("CALCULATOR_INVALID_EXPRESSION", "表达式语法无效。") from exc
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise ToolFailure("CALCULATOR_LIMIT", "表达式过于复杂。")
    try:
        value = _evaluate(tree)
    except ZeroDivisionError as exc:
        raise ToolFailure("CALCULATOR_DIVISION_BY_ZERO", "不能除以零。") from exc
    except (OverflowError, ValueError) as exc:
        raise ToolFailure("CALCULATOR_LIMIT", "计算结果超出允许范围。") from exc
    return ToolResult(ok=True, tool="calculator", data={"value": value})


def calculator_spec() -> ToolSpec:
    return ToolSpec(
        name="calculator",
        description="安全计算基础算术表达式；不支持变量、函数、属性访问或代码执行。",
        parameters={
            "type": "object",
            "properties": {
                "expression": {"type": "string", "minLength": 1, "maxLength": _MAX_EXPRESSION_LENGTH}
            },
            "required": ["expression"],
            "additionalProperties": False,
        },
        handler=_calculator,
    )

