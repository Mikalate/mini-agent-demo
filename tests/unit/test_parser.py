from mini_agent.core.parser import parse_response
from mini_agent.llm.base import LLMResponse


def response(message, finish_reason="stop") -> LLMResponse:
    return LLMResponse(None, "fake", message, finish_reason)


def test_parses_final_response() -> None:
    parsed = parse_response(response({"role": "assistant", "content": "答案"}))
    assert parsed.kind == "final"
    assert parsed.content == "答案"


def test_parses_tool_call_and_preserves_reasoning_content() -> None:
    parsed = parse_response(
        response(
            {
                "role": "assistant",
                "content": "调用计算器",
                "reasoning_content": "private-protocol-value",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": '{"expression":"6*7"}'},
                    }
                ],
            },
            "tool_calls",
        )
    )
    assert parsed.kind == "tool_calls"
    assert parsed.reasoning_content == "private-protocol-value"
    assert parsed.tool_calls[0].call is not None
    assert parsed.tool_calls[0].call.arguments == {"expression": "6*7"}


def test_invalid_arguments_are_structured_but_call_id_is_retained() -> None:
    parsed = parse_response(
        response(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-bad",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": "{"},
                    }
                ],
            },
            "tool_calls",
        )
    )
    assert parsed.kind == "tool_calls"
    assert parsed.tool_calls[0].id == "call-bad"
    assert parsed.tool_calls[0].issue is not None
    assert parsed.tool_calls[0].issue.code == "INVALID_ARGUMENTS_JSON"


def test_truncated_response_never_exposes_tool_calls_for_execution() -> None:
    parsed = parse_response(
        response(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": '{"expression":"6*7"}'},
                    }
                ],
            },
            "length",
        )
    )
    assert parsed.kind == "invalid"
    assert parsed.tool_calls == []


def test_missing_call_id_invalidates_whole_assistant_message() -> None:
    parsed = parse_response(
        response(
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "calculator", "arguments": "{}"},
                    }
                ],
            },
            "tool_calls",
        )
    )
    assert parsed.kind == "invalid"

