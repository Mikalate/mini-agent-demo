from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from mini_agent.core.agent import Agent
from mini_agent.llm.base import LLMError, LLMResponse, LLMUsage
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry


def tool_call(call_id: str, expression: str, *, tokens: int = 1) -> LLMResponse:
    return LLMResponse(
        "id",
        "fake",
        {
            "role": "assistant",
            "content": "调用计算器",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": json.dumps({"expression": expression}),
                    },
                }
            ],
        },
        "tool_calls",
        LLMUsage(total_tokens=tokens),
    )


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, messages, tools, **kwargs):
        self.requests.append(messages)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_agent(settings, tmp_path, llm, **changes):
    configured = replace(settings, **changes)
    store = SessionStore(tmp_path / "agent.db")
    return (
        Agent(
            settings=configured,
            llm=llm,
            registry=build_default_registry(),
            store=store,
        ),
        store,
    )


def test_repeated_same_call_and_result_stops_with_no_progress(settings, tmp_path) -> None:
    llm = FakeLLM([tool_call("one", "6*7"), tool_call("two", "6*7")])
    agent, _ = make_agent(settings, tmp_path, llm, max_repeated_calls=2)

    result = asyncio.run(agent.run_turn("user", "repeat", "一直计算"))

    assert result.status == "incomplete"
    assert result.error_code == "NO_PROGRESS"
    assert result.state.tool_step == 2
    assert result.state.repeated_call_count == 2
    assert len(llm.requests) == 2


def test_round_limit_stops_without_requesting_extra_response(settings, tmp_path) -> None:
    llm = FakeLLM([tool_call("one", "1+1"), tool_call("two", "2+2")])
    agent, _ = make_agent(
        settings, tmp_path, llm, max_llm_rounds_per_turn=2, max_repeated_calls=3
    )

    result = asyncio.run(agent.run_turn("user", "rounds", "计算"))

    assert result.error_code == "MAX_ROUNDS_REACHED"
    assert result.state.round == 2
    assert len(llm.requests) == 2


def test_tool_limit_rejects_batch_before_any_execution(settings, tmp_path) -> None:
    response = LLMResponse(
        "id",
        "fake",
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "one",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expression":"1+1"}',
                    },
                },
                {
                    "id": "two",
                    "type": "function",
                    "function": {
                        "name": "calculator",
                        "arguments": '{"expression":"2+2"}',
                    },
                },
            ],
        },
        "tool_calls",
    )
    agent, _ = make_agent(
        settings, tmp_path, FakeLLM([response]), max_tool_calls_per_turn=1
    )

    result = asyncio.run(agent.run_turn("user", "tools", "计算两次"))

    assert result.error_code == "MAX_TOOL_CALLS_REACHED"
    assert result.state.tool_step == 0


def test_token_limit_stops_before_tool_side_effect(settings, tmp_path) -> None:
    agent, _ = make_agent(
        settings,
        tmp_path,
        FakeLLM([tool_call("one", "6*7", tokens=10)]),
        max_total_tokens_per_turn=10,
    )

    result = asyncio.run(agent.run_turn("user", "tokens", "计算"))

    assert result.error_code == "MAX_TOTAL_TOKENS_REACHED"
    assert result.state.total_tokens == 10
    assert result.state.tool_step == 0


def test_repeated_empty_response_stops_with_protocol_error(settings, tmp_path) -> None:
    empty = LLMResponse("id", "fake", {"role": "assistant", "content": ""}, "stop")
    agent, _ = make_agent(
        settings, tmp_path, FakeLLM([empty, empty]), max_protocol_errors=2
    )

    result = asyncio.run(agent.run_turn("user", "empty", "回答"))

    assert result.error_code == "EMPTY_MODEL_RESPONSE"
    assert result.status == "incomplete"


def test_fatal_api_error_and_cancellation_have_determined_status(settings, tmp_path) -> None:
    fatal = LLMError(
        "LLM_AUTH_FAILED", "认证失败", retryable=False, attempts=1, status_code=401
    )
    fatal_agent, _ = make_agent(settings, tmp_path / "fatal", FakeLLM([fatal]))
    fatal_result = asyncio.run(fatal_agent.run_turn("user", "fatal", "回答"))

    class CancelLLM:
        async def complete(self, messages, tools, **kwargs):
            raise asyncio.CancelledError

    cancel_agent, cancel_store = make_agent(
        settings, tmp_path / "cancel", CancelLLM()
    )
    cancel_result = asyncio.run(cancel_agent.run_turn("user", "cancel", "回答"))

    assert fatal_result.error_code == "LLM_AUTH_FAILED"
    assert fatal_result.status == "incomplete"
    assert cancel_result.status == "interrupted"
    assert cancel_result.error_code == "INTERRUPTED"
    assert "继续" in cancel_result.content
    assert [message.role for message in cancel_store.get_messages("user", "cancel")] == [
        "user"
    ]


def _final(usage: LLMUsage | None = None) -> LLMResponse:
    return LLMResponse(
        "id",
        "fake",
        {"role": "assistant", "content": "完成"},
        "stop",
        usage or LLMUsage(),
    )


def test_cost_estimate_uses_input_output_and_cache_hit_prices(
    settings, tmp_path
) -> None:
    usage = LLMUsage(
        prompt_tokens=1_100_000,
        completion_tokens=200_000,
        total_tokens=1_300_000,
        prompt_cache_hit_tokens=100_000,
        prompt_cache_miss_tokens=1_000_000,
    )
    agent, _ = make_agent(settings, tmp_path, FakeLLM([_final(usage)]))

    result = asyncio.run(agent.run_turn("user", "cost", "回答"))

    assert result.status == "completed"
    assert result.state.prompt_tokens == 1_100_000
    assert result.state.completion_tokens == 200_000
    assert result.state.cost_usd == pytest.approx(
        (1_000_000 * 0.27 + 100_000 * 0.07 + 200_000 * 1.10) / 1_000_000
    )


def test_cost_falls_back_to_miss_price_when_cache_fields_missing(
    settings, tmp_path
) -> None:
    usage = LLMUsage(prompt_tokens=1_000_000, total_tokens=1_000_000)
    agent, _ = make_agent(settings, tmp_path, FakeLLM([_final(usage)]))

    result = asyncio.run(agent.run_turn("user", "costfb", "回答"))

    assert result.state.cost_usd == pytest.approx(0.27)


def test_cost_is_zero_without_usage(settings, tmp_path) -> None:
    agent, _ = make_agent(settings, tmp_path, FakeLLM([_final()]))

    result = asyncio.run(agent.run_turn("user", "cost0", "回答"))

    assert result.state.cost_usd == 0.0


def test_run_level_retries_once_for_transient_first_call(settings, tmp_path) -> None:
    transient = LLMError("LLM_TIMEOUT", "瞬时失败", retryable=True, attempts=1)
    agent, _ = make_agent(settings, tmp_path, FakeLLM([transient, _final()]))

    result = asyncio.run(agent.run_turn("user", "retry", "回答"))

    assert result.status == "completed"
    assert result.state.round == 1  # 重试后一次成功


def test_run_level_retry_happens_only_once(settings, tmp_path) -> None:
    transient = LLMError("LLM_TIMEOUT", "瞬时失败", retryable=True, attempts=1)
    agent, _ = make_agent(settings, tmp_path, FakeLLM([transient, transient]))

    result = asyncio.run(agent.run_turn("user", "retry2", "回答"))

    assert result.status == "incomplete"
    assert result.error_code == "LLM_TIMEOUT"
    assert result.state.round == 1  # 重试一次后再失败即终止


def test_run_level_retry_skipped_after_tool_execution(settings, tmp_path) -> None:
    transient = LLMError("LLM_TIMEOUT", "瞬时失败", retryable=True, attempts=1)
    agent, _ = make_agent(
        settings, tmp_path, FakeLLM([tool_call("one", "6*7"), transient])
    )

    result = asyncio.run(agent.run_turn("user", "retry3", "计算"))

    assert result.status == "incomplete"
    assert result.error_code == "LLM_TIMEOUT"
    assert result.state.round == 2  # 已执行工具后不自动重试
    assert result.state.tool_step == 1
