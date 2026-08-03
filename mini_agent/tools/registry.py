from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from mini_agent.core.models import ToolCall
from mini_agent.tools.base import (
    ToolContext,
    ToolErrorInfo,
    ToolFailure,
    ToolResult,
    ToolSpec,
)


LOGGER = logging.getLogger(__name__)
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class ToolRegistrationError(ValueError):
    pass


class ToolValidationError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ToolRegistry:
    def __init__(self, *, max_result_chars: int = 12_000):
        self._specs: dict[str, ToolSpec] = {}
        self.max_result_chars = max_result_chars

    def register(self, spec: ToolSpec) -> None:
        if not _TOOL_NAME.fullmatch(spec.name):
            raise ToolRegistrationError(f"非法工具名：{spec.name!r}")
        if spec.name in self._specs:
            raise ToolRegistrationError(f"工具重复注册：{spec.name}")
        try:
            Draft202012Validator.check_schema(spec.parameters)
        except SchemaError as exc:
            raise ToolRegistrationError(f"工具 {spec.name} 的 JSON Schema 无效。") from exc
        self._specs[spec.name] = spec

    def list_specs(self) -> list[ToolSpec]:
        return list(self._specs.values())

    def as_llm_tools(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in self._specs.values()
        ]

    def validate(self, name: str, arguments: dict[str, Any]) -> ToolSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise ToolValidationError("UNKNOWN_TOOL", f"未注册的工具：{name}")
        if not isinstance(arguments, dict):
            raise ToolValidationError("SCHEMA_VALIDATION_FAILED", "工具参数必须是 JSON object。")
        try:
            Draft202012Validator(spec.parameters).validate(arguments)
            if spec.argument_validator is not None:
                spec.argument_validator(arguments)
        except ValidationError as exc:
            location = ".".join(str(item) for item in exc.absolute_path)
            prefix = f"参数 {location}：" if location else ""
            raise ToolValidationError(
                "SCHEMA_VALIDATION_FAILED", f"{prefix}{exc.message}"
            ) from exc
        except ToolFailure as exc:
            raise ToolValidationError(exc.code, exc.message) from exc
        return spec

    async def invoke(self, call: ToolCall, context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        try:
            if call.arguments is None:
                raise ToolValidationError(
                    "INVALID_ARGUMENTS_JSON", "工具参数不是有效 JSON object。"
                )
            spec = self.validate(call.name, call.arguments)
        except ToolValidationError as exc:
            return self._finish(
                ToolResult(
                    ok=False,
                    tool=call.name,
                    error=ToolErrorInfo(exc.code, exc.message, retryable=False),
                ),
                started,
            )

        try:
            result = await spec.handler(call.arguments, context)
            if result.tool != call.name:
                raise RuntimeError("工具 handler 返回了不匹配的工具名。")
        except ToolFailure as exc:
            result = ToolResult(
                ok=False,
                tool=call.name,
                error=ToolErrorInfo(exc.code, exc.message, exc.retryable),
            )
        except Exception:
            LOGGER.exception("Tool %s failed", call.name)
            result = ToolResult(
                ok=False,
                tool=call.name,
                error=ToolErrorInfo(
                    "TOOL_EXECUTION_FAILED", "工具执行失败，请调整请求或稍后重试。", False
                ),
            )
        return self._finish(result, started)

    def _finish(self, result: ToolResult, started: float) -> ToolResult:
        result.meta["duration_ms"] = max(0, round((time.perf_counter() - started) * 1000))
        serialized = json.dumps(result.as_dict(), ensure_ascii=False, default=str)
        if len(serialized) > self.max_result_chars:
            result.data = {"preview": serialized[: self.max_result_chars - 500]}
            result.meta["truncated"] = True
        return result


def build_default_registry() -> ToolRegistry:
    from mini_agent.tools.calculator import calculator_spec
    from mini_agent.tools.search import search_spec
    from mini_agent.tools.todo import todo_spec
    from mini_agent.tools.weather import weather_spec

    registry = ToolRegistry()
    for spec in (calculator_spec(), search_spec(), todo_spec(), weather_spec()):
        registry.register(spec)
    return registry
