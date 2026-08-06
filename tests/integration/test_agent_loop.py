from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path

from mini_agent.core.agent import Agent
from mini_agent.llm.base import LLMResponse, LLMUsage
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry


def final(content: str) -> LLMResponse:
    return LLMResponse("id", "fake", {"role": "assistant", "content": content}, "stop")


def calls(*items, reasoning="private-reasoning") -> LLMResponse:
    tool_calls = [
        {
            "id": call_id,
            "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)},
        }
        for call_id, name, arguments in items
    ]
    return LLMResponse(
        "id",
        "fake",
        {
            "role": "assistant",
            "content": "我将使用工具。",
            "reasoning_content": reasoning,
            "tool_calls": tool_calls,
        },
        "tool_calls",
        LLMUsage(total_tokens=5),
    )


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, messages, tools, **kwargs):
        self.requests.append((copy.deepcopy(messages), copy.deepcopy(tools)))
        return self.responses.pop(0)


def make_agent(settings, tmp_path: Path, llm: FakeLLM):
    store = SessionStore(tmp_path / "agent.db")
    return Agent(
        settings=settings,
        llm=llm,
        registry=build_default_registry(),
        store=store,
    ), store


def test_direct_reply(settings, tmp_path: Path) -> None:
    llm = FakeLLM([final("直接回答")])
    agent, store = make_agent(settings, tmp_path, llm)

    result = asyncio.run(agent.run_turn("user_a", "direct", "你好"))

    assert result.status == "completed"
    assert result.content == "直接回答"
    assert [message.role for message in store.get_messages("user_a", "direct")] == [
        "user",
        "assistant",
    ]


def test_one_tool_round_trips_reasoning_and_call_id(settings, tmp_path: Path) -> None:
    llm = FakeLLM(
        [calls(("calc-1", "calculator", {"expression": "6*7"})), final("结果是 42")]
    )
    agent, store = make_agent(settings, tmp_path, llm)

    result = asyncio.run(agent.run_turn("user_a", "calculator", "计算 6*7"))

    assert result.status == "completed"
    second_messages = llm.requests[1][0]
    assistant = next(message for message in second_messages if message.get("tool_calls"))
    tool = next(message for message in second_messages if message["role"] == "tool")
    assert assistant["reasoning_content"] == "private-reasoning"
    assert assistant["tool_calls"][0]["id"] == tool["tool_call_id"] == "calc-1"
    assert json.loads(tool["content"])["data"]["value"] == 42
    assert store.get_messages("user_a", "calculator")[-1].content == "结果是 42"


def test_multiple_tool_calls_in_one_response(settings, tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            calls(
                ("calc-1", "calculator", {"expression": "20+22"}),
                ("weather-1", "weather", {"city": "上海"}),
            ),
            final("计算和天气都已处理"),
        ]
    )
    agent, _ = make_agent(settings, tmp_path, llm)

    result = asyncio.run(agent.run_turn("user_a", "multi", "计算并查天气"))

    assert result.status == "completed"
    tool_messages = [m for m in llm.requests[1][0] if m["role"] == "tool"]
    assert [message["tool_call_id"] for message in tool_messages] == [
        "calc-1",
        "weather-1",
    ]


def test_tool_error_is_returned_and_model_can_correct(settings, tmp_path: Path) -> None:
    llm = FakeLLM(
        [
            calls(("bad-1", "calculator", {"expression": "1/0"})),
            calls(("fixed-1", "calculator", {"expression": "6*7"})),
            final("修正后结果是 42"),
        ]
    )
    agent, _ = make_agent(settings, tmp_path, llm)

    result = asyncio.run(agent.run_turn("user_a", "correct", "计算"))

    first_error = json.loads(llm.requests[1][0][-1]["content"])
    corrected = json.loads(llm.requests[2][0][-1]["content"])
    assert first_error["error"]["code"] == "CALCULATOR_DIVISION_BY_ZERO"
    assert corrected["data"]["value"] == 42
    assert result.status == "completed"


def test_invalid_json_is_not_executed_and_can_be_corrected(settings, tmp_path: Path) -> None:
    malformed = LLMResponse(
        "id",
        "fake",
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "bad-json",
                    "type": "function",
                    "function": {"name": "calculator", "arguments": "{"},
                }
            ],
            "reasoning_content": "opaque",
        },
        "tool_calls",
    )
    llm = FakeLLM(
        [malformed, calls(("fixed", "calculator", {"expression": "6*7"})), final("42")]
    )
    agent, _ = make_agent(settings, tmp_path, llm)

    result = asyncio.run(agent.run_turn("user_a", "bad-json", "计算"))

    error_result = json.loads(llm.requests[1][0][-1]["content"])
    assert error_result["error"]["code"] == "INVALID_ARGUMENTS_JSON"
    assert result.status == "completed"

