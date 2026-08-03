import asyncio
from types import SimpleNamespace

from mini_agent.llm.deepseek import DeepSeekClient
from mini_agent.llm.base import LLMError


class FakeMessage:
    reasoning_content = "must-round-trip"
    model_extra = {}

    def model_dump(self, **kwargs):
        return {"role": "assistant", "content": None, "tool_calls": []}


class FakeUsage:
    def model_dump(self):
        return {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}


def test_adapter_omits_tool_choice_and_preserves_reasoning(settings) -> None:
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            id="response-1",
            model="fake-model",
            choices=[SimpleNamespace(message=FakeMessage(), finish_reason="tool_calls")],
            usage=FakeUsage(),
        )

    client = DeepSeekClient(settings)
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    result = asyncio.run(client.complete([{"role": "user", "content": "hi"}], []))

    assert "tool_choice" not in captured
    assert captured["extra_body"] == {"thinking": {"type": "enabled"}}
    assert captured["reasoning_effort"] == "max"
    assert result.assistant_message["reasoning_content"] == "must-round-trip"
    assert result.usage.total_tokens == 5


def test_retryable_api_errors_back_off_and_report_each_retry(settings) -> None:
    attempts = 0
    delays = []
    retry_events = []

    def create(**kwargs):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("temporary")
        return SimpleNamespace(
            id="response-1",
            model="fake-model",
            choices=[SimpleNamespace(message=FakeMessage(), finish_reason="stop")],
            usage=FakeUsage(),
        )

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
