from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from mini_agent.core.models import ToolCall
from mini_agent.llm.base import LLMResponse


ParseKind = Literal["final", "tool_calls", "invalid", "retry"]


@dataclass(frozen=True, slots=True)
class ParseIssue:
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class ParsedToolCall:
    id: str
    name: str
    raw_arguments: str
    call: ToolCall | None = None
    issue: ParseIssue | None = None


@dataclass(frozen=True, slots=True)
class ParsedAssistant:
    kind: ParseKind
    finish_reason: str | None
    content: str | None
    reasoning_content: str | None
    tool_calls: list[ParsedToolCall] = field(default_factory=list)
    issue: ParseIssue | None = None


def _parse_tool_call(raw: Any, index: int) -> ParsedToolCall:
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(exclude_none=True)
    if not isinstance(raw, dict):
        return ParsedToolCall(
            id=f"invalid_{index}", name="", raw_arguments="",
            issue=ParseIssue("INVALID_TOOL_CALL", "工具调用不是 object。")
        )
    call_id = raw.get("id")
    call_type = raw.get("type")
    function = raw.get("function")
    if not isinstance(call_id, str) or not call_id:
        return ParsedToolCall(
            id=f"invalid_{index}", name="", raw_arguments="",
            issue=ParseIssue("INVALID_TOOL_CALL", "工具调用缺少 id。")
        )
    if call_type != "function" or not isinstance(function, dict):
        return ParsedToolCall(
            id=call_id, name="", raw_arguments="",
            issue=ParseIssue("INVALID_TOOL_CALL", "仅支持 type=function 的工具调用。")
        )
    name = function.get("name")
    raw_arguments = function.get("arguments")
    if not isinstance(name, str) or not name:
        return ParsedToolCall(
            id=call_id, name="", raw_arguments=str(raw_arguments or ""),
            issue=ParseIssue("INVALID_TOOL_CALL", "工具调用缺少 function.name。")
        )
    if not isinstance(raw_arguments, str):
        return ParsedToolCall(
            id=call_id, name=name, raw_arguments="",
            issue=ParseIssue("INVALID_ARGUMENTS_JSON", "function.arguments 必须是 JSON 字符串。")
        )
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return ParsedToolCall(
            id=call_id, name=name, raw_arguments=raw_arguments,
            issue=ParseIssue("INVALID_ARGUMENTS_JSON", "工具参数不是有效 JSON。")
        )
    if not isinstance(arguments, dict):
        return ParsedToolCall(
            id=call_id, name=name, raw_arguments=raw_arguments,
            issue=ParseIssue("SCHEMA_VALIDATION_FAILED", "工具参数必须是 JSON object。")
        )
    return ParsedToolCall(
        id=call_id,
        name=name,
        raw_arguments=raw_arguments,
        call=ToolCall(
            id=call_id, name=name, arguments=arguments, raw_arguments=raw_arguments
        ),
    )


def parse_response(response: LLMResponse) -> ParsedAssistant:
    message = response.assistant_message
    content_raw = message.get("content")
    content = content_raw.strip() if isinstance(content_raw, str) and content_raw.strip() else None
    reasoning_raw = message.get("reasoning_content")
    reasoning_content = reasoning_raw if isinstance(reasoning_raw, str) else None
    raw_calls = message.get("tool_calls") or []
    if not isinstance(raw_calls, list):
        return ParsedAssistant(
            kind="invalid", finish_reason=response.finish_reason, content=content,
            reasoning_content=reasoning_content,
            issue=ParseIssue("INVALID_TOOL_CALL", "assistant.tool_calls 必须是数组。")
        )

    if response.finish_reason == "insufficient_system_resource":
        return ParsedAssistant(
            kind="retry", finish_reason=response.finish_reason, content=content,
            reasoning_content=reasoning_content,
            issue=ParseIssue("LLM_SERVER_ERROR", "模型服务资源暂时不足。")
        )
    if response.finish_reason in {"length", "content_filter"}:
        return ParsedAssistant(
            kind="invalid", finish_reason=response.finish_reason, content=content,
            reasoning_content=reasoning_content,
            issue=ParseIssue(
                "INCOMPLETE_MODEL_RESPONSE", "模型输出被截断或过滤，不能执行其中的工具调用。"
            )
        )

    tool_calls = [_parse_tool_call(raw, index) for index, raw in enumerate(raw_calls)]
    if tool_calls:
        malformed = next(
            (
                call.issue
                for call in tool_calls
                if call.issue is not None and call.issue.code == "INVALID_TOOL_CALL"
            ),
            None,
        )
        if malformed is not None:
            return ParsedAssistant(
                kind="invalid",
                finish_reason=response.finish_reason,
                content=content,
                reasoning_content=reasoning_content,
                issue=malformed,
            )
        return ParsedAssistant(
            kind="tool_calls", finish_reason=response.finish_reason, content=content,
            reasoning_content=reasoning_content, tool_calls=tool_calls
        )
    if content:
        return ParsedAssistant(
            kind="final", finish_reason=response.finish_reason, content=content,
            reasoning_content=reasoning_content
        )
    return ParsedAssistant(
        kind="invalid", finish_reason=response.finish_reason, content=None,
        reasoning_content=reasoning_content,
        issue=ParseIssue("EMPTY_MODEL_RESPONSE", "模型没有返回文本或工具调用。")
    )
