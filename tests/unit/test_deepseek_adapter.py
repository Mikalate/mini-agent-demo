import asyncio
from types import SimpleNamespace

from mini_agent.llm.deepseek import DeepSeekClient
from mini_agent.llm.base import LLMError


class FakeUsage:
    def model_dump(self):
        return {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
            "prompt_cache_hit_tokens": 1,
            "prompt_cache_miss_tokens": 2,
            "completion_tokens_details": {"reasoning_tokens": 1},
        }


class FakeChunk:
    def __init__(
        self,
        *,
        content=None,
        reasoning=None,
        tool_calls=None,
        finish_reason=None,
        usage=None,
    ):
        attrs = {"content": content, "tool_calls": tool_calls}
        if reasoning is not None:
            attrs["reasoning_content"] = reasoning
        self.choices = [
            SimpleNamespace(delta=SimpleNamespace(**attrs), finish_reason=finish_reason)
        ]
        self.usage = usage


def stream_response():
    return iter(
        [
            FakeChunk(content="你好"),
            FakeChunk(reasoning="隐藏推理"),
            FakeChunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id="call-1",
                        function=SimpleNamespace(
                            name="calculator", arguments='{"expr'
                        ),
                    )
                ]
            ),
            FakeChunk(
                tool_calls=[
                    SimpleNamespace(
                        index=0,
                        id=None,
                        function=SimpleNamespace(name=None, arguments='ession": "6*7"}'),
                    )
                ]
            ),
            FakeChunk(finish_reason="stop", usage=FakeUsage()),
        ]
    )


def test_adapter_streams_with_include_usage_and_merges_deltas(settings) -> None:
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return stream_response()

    client = DeepSeekClient(settings)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))

    assert "tool_choice" not in captured
    assert captured["stream"] is True
    assert captured["stream_options"] == {"include_usage": True}
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "max"
    assert result.assistant_message["content"] == "你好"
    assert result.assistant_message["reasoning_content"] == "隐藏推理"
    call = result.assistant_message["tool_calls"][0]
    assert call["id"] == "call-1"
    assert call["function"]["name"] == "calculator"
    assert call["function"]["arguments"] == '{"expression": "6*7"}'
    assert result.finish_reason == "stop"
    assert result.usage.total_tokens == 5
    assert result.usage.reasoning_tokens == 1


def test_streaming_budget_aborts_when_output_budget_exceeded(settings) -> None:
    client = DeepSeekClient(settings)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: stream_response())
        )
    )

    try:
        asyncio.run(
            client.complete([{"role": "user", "content": "hi"}], [], max_output_tokens=1)
        )
    except LLMError as exc:
        assert exc.code == "MAX_TOTAL_TOKENS_REACHED"
        assert not exc.retryable
    else:
        raise AssertionError("expected MAX_TOTAL_TOKENS_REACHED")


def test_retryable_api_errors_back_off_and_report_each_retry(settings) -> None:
    attempts = 0
    delays = []
    retry_events = []

    def create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return stream_response()

    async def sleep(delay):
        delays.append(delay)

    client = DeepSeekClient(
        settings, sleep=sleep, retry_observer=retry_events.append
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    client._classify_error = lambda exc, *, attempts: LLMError(
        "LLM_TIMEOUT", "temporary", retryable=True, attempts=attempts
    )

    result = asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))

    assert result.attempts == 3
    assert attempts == 3
    assert len(delays) == 2
    assert [event["attempt"] for event in retry_events] == [1, 2]
    assert all(event["code"] == "LLM_TIMEOUT" for event in retry_events)


def test_non_retryable_api_error_stops_immediately(settings) -> None:
    delays = []
    retry_events = []

    async def sleep(delay):
        delays.append(delay)

    client = DeepSeekClient(
        settings, sleep=sleep, retry_observer=retry_events.append
    )
    client._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("fatal"))
            )
        )
    )
    client._classify_error = lambda exc, *, attempts: LLMError(
        "LLM_AUTH_FAILED",
        "fatal",
        retryable=False,
        attempts=attempts,
        status_code=401,
    )

    try:
        asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))
    except LLMError as exc:
        assert exc.code == "LLM_AUTH_FAILED"
        assert exc.attempts == 1
    else:
        raise AssertionError("expected LLMError")
    assert delays == []
    assert retry_events == []
