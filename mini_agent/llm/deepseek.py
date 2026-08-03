from __future__ import annotations

import asyncio
import random
from collections.abc import Callable
from typing import Any

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
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        max_attempts = max(1, self.settings.deepseek_max_retries)
        for attempt in range(1, max_attempts + 1):
            try:
                response = await asyncio.to_thread(self._create, messages, tools)
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

    def _create(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]):
        kwargs: dict[str, Any] = {
            "model": self.settings.deepseek_model,
            "messages": messages,
            "max_tokens": self.settings.deepseek_max_tokens,
            "stream": False,
            "extra_body": {"thinking": {"type": self.settings.deepseek_thinking}},
        }
        if tools:
            kwargs["tools"] = tools
        if self.settings.deepseek_thinking == "enabled":
            kwargs["reasoning_effort"] = self.settings.deepseek_reasoning_effort
        # tool_choice is deliberately omitted: DeepSeek must choose reply vs tool.
        return self._client.chat.completions.create(**kwargs)

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
