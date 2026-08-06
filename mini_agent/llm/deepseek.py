from __future__ import annotations

import asyncio
import random
from types import SimpleNamespace
from typing import Any
from collections.abc import Callable

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)

from mini_agent.config import Settings
from mini_agent.llm.base import LLMError, LLMResponse, LLMUsage
from mini_agent.llm.tokenizer import count_tokens


class DeepSeekClient:
    """DeepSeek Chat Completions transport; it never executes tools."""

    def __init__(
        self,
        settings: Settings,
        *,
        sleep=asyncio.sleep,
        retry_observer: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.settings = settings
        self._sleep = sleep
        self._retry_observer = retry_observer
        self._client = OpenAI(
            api_key=settings.require_deepseek_api_key(),
            base_url=settings.deepseek_base_url,
            timeout=float(settings.deepseek_timeout_seconds),
            max_retries=0,
        )

    def set_retry_observer(
        self, observer: Callable[[dict[str, Any]], None] | None
    ) -> None:
        self._retry_observer = observer

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        max_output_tokens: int | None = None,
    ) -> LLMResponse:
        max_attempts = max(1, self.settings.deepseek_max_retries)
        for attempt in range(1, max_attempts + 1):
            try:
                response = await asyncio.to_thread(
                    self._create_stream, messages, tools, max_output_tokens
                )
                return self._normalize(response, attempts=attempt)
            except Exception as exc:
                error = self._classify_error(exc, attempts=attempt)
                if not error.retryable or attempt >= max_attempts:
                    raise error from exc
                delay = (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                if self._retry_observer is not None:
                    try:
                        self._retry_observer(
                            {
                                "scope": "api",
                                "attempt": attempt,
                                "next_attempt": attempt + 1,
                                "code": error.code,
                                "status_code": error.status_code,
                                "delay_ms": round(delay * 1000),
                                "message": (
                                    f"模型请求第 {attempt} 次失败，"
                                    f"将在 {delay:.2f} 秒后重试。"
                                ),
                            }
                        )
                    except Exception:
                        # Observation is best-effort and must not change retry flow.
                        pass
                await self._sleep(delay)
        raise AssertionError("unreachable")

    def _create_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        max_output_tokens: int | None,
    ) -> Any:
        kwargs: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "max_tokens": self.settings.deepseek_max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "extra_body": {"thinking": {"type": self.settings.deepseek_thinking}},
        }
        if tools:
            kwargs["tools"] = tools
        if self.settings.deepseek_thinking == "enabled":
            kwargs["reasoning_effort"] = self.settings.deepseek_reasoning_effort
        # tool_choice is deliberately omitted: DeepSeek must choose reply vs tool.
        stream = self._client.chat.completions.create(**kwargs)
        return self._accumulate_stream(stream, max_output_tokens)

    @staticmethod
    def _accumulate_stream(stream: Any, max_output_tokens: int | None) -> Any:
        """Consume an SSE stream into the same shape as a non-streaming response.

        `reasoning_content` and `tool_calls` arrive as deltas; finish_reason and
        usage only appear on the final chunk. When a max output budget is set,
        streamed deltas are counted and the stream is aborted before the budget
        is exceeded.
        """

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage_raw: dict[str, Any] | None = None
        budget_tokens = 0

        for chunk in stream:
            if chunk.usage is not None:
                usage_raw = (
                    chunk.usage.model_dump()
                    if hasattr(chunk.usage, "model_dump")
                    else dict(chunk.usage)
                )
                if chunk.choices and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                continue
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            delta_text = ""
            if delta.content:
                content_parts.append(delta.content)
                delta_text += delta.content
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                reasoning_parts.append(reasoning)
                delta_text += reasoning
            if delta.tool_calls:
                for call in delta.tool_calls:
                    entry = tool_calls.setdefault(
                        call.index, {"id": "", "name": "", "args": []}
                    )
                    if call.id:
                        entry["id"] = call.id
                    if call.function and call.function.name:
                        entry["name"] = call.function.name
                    if call.function and call.function.arguments:
                        entry["args"].append(call.function.arguments)
                        delta_text += call.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            if max_output_tokens is not None and delta_text:
                budget_tokens += count_tokens(delta_text)
                if budget_tokens >= max_output_tokens:
                    raise LLMError(
                        "MAX_TOTAL_TOKENS_REACHED",
                        "流式响应已达到 token 预算，已提前终止本轮生成。",
                        retryable=False,
                        attempts=1,
                    )

        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": "".join(content_parts) or None,
        }
        if reasoning_parts:
            assistant_message["reasoning_content"] = "".join(reasoning_parts)
        if tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": entry["id"],
                    "type": "function",
                    "function": {"name": entry["name"], "arguments": "".join(entry["args"])},
                }
                for entry in tool_calls.values()
            ]

        class _StreamMessage:
            def __init__(self, data: dict[str, Any]):
                self.__dict__.update(data)

            def model_dump(self, *, exclude_none: bool = True) -> dict[str, Any]:
                return {
                    key: value
                    for key, value in self.__dict__.items()
                    if value is not None
                }

        choice = SimpleNamespace(
            message=_StreamMessage(assistant_message),
            finish_reason=finish_reason,
        )
        return SimpleNamespace(
            id=None,
            model=None,
            choices=[choice],
            usage=SimpleNamespace(model_dump=lambda: usage_raw or {}),
        )

    @staticmethod
    def _normalize(response: Any, *, attempts: int) -> LLMResponse:
        if not response.choices:
            raise LLMError(
                "EMPTY_MODEL_RESPONSE",
                "模型响应中没有 choices。",
                retryable=False,
                attempts=attempts,
            )
        choice = response.choices[0]
        message = choice.message
        assistant_message = message.model_dump(exclude_none=True)

        reasoning_content = getattr(message, "reasoning_content", None)
        if reasoning_content is None:
            model_extra = getattr(message, "model_extra", None) or {}
            reasoning_content = model_extra.get("reasoning_content")
        if reasoning_content is not None:
            assistant_message["reasoning_content"] = reasoning_content

        usage_raw = response.usage.model_dump() if response.usage else {}
        completion_details = usage_raw.get("completion_tokens_details") or {}
        usage = LLMUsage(
            prompt_tokens=usage_raw.get("prompt_tokens", 0) or 0,
            completion_tokens=usage_raw.get("completion_tokens", 0) or 0,
            total_tokens=usage_raw.get("total_tokens", 0) or 0,
            prompt_cache_hit_tokens=usage_raw.get("prompt_cache_hit_tokens", 0) or 0,
            prompt_cache_miss_tokens=usage_raw.get("prompt_cache_miss_tokens", 0) or 0,
            reasoning_tokens=completion_details.get("reasoning_tokens", 0) or 0,
        )
        return LLMResponse(
            response_id=getattr(response, "id", None),
            model=getattr(response, "model", None),
            assistant_message=assistant_message,
            finish_reason=choice.finish_reason,
            usage=usage,
            attempts=attempts,
        )

    @staticmethod
    def _classify_error(exc: Exception, *, attempts: int) -> LLMError:
        if isinstance(exc, LLMError):
            return exc
        status_code = getattr(exc, "status_code", None)
        if isinstance(exc, RateLimitError) or status_code == 429:
            return LLMError(
                "LLM_RATE_LIMITED", "模型服务繁忙（429），重试次数已用尽。",
                retryable=True, attempts=attempts, status_code=429
            )
        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            return LLMError(
                "LLM_TIMEOUT", "连接模型服务超时或失败。",
                retryable=True, attempts=attempts
            )
        if isinstance(exc, InternalServerError) or status_code in {500, 503}:
            return LLMError(
                "LLM_SERVER_ERROR", "模型服务暂时不可用。",
                retryable=True, attempts=attempts, status_code=status_code
            )
        if isinstance(exc, APIStatusError):
            code = "LLM_AUTH_FAILED" if status_code in {401, 403} else "LLM_BAD_REQUEST"
            message = (
                "DeepSeek API key 无效或没有权限。"
                if status_code in {401, 403}
                else f"模型请求被拒绝（HTTP {status_code}），请检查模型名和消息协议。"
            )
            return LLMError(
                code, message, retryable=False, attempts=attempts, status_code=status_code
            )
        return LLMError(
            "LLM_REQUEST_FAILED", "模型请求失败。", retryable=False, attempts=attempts
        )
