from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from mini_agent.core.models import Message
from mini_agent.core.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    summary_user_prompt,
)
from mini_agent.core.trace import compact_text, redact
from mini_agent.llm.base import LLMClient, LLMError, LLMUsage
from mini_agent.sessions.store import SessionStore


_SUMMARY_HEADINGS = ("## 已确认事实", "## 用户偏好", "## 工具结果", "## 未解决事项")


@dataclass(frozen=True, slots=True)
class ContextBuildResult:
    messages: list[dict[str, Any]]
    compacted_messages: int = 0
    summary_version: int = 0
    fallback_used: bool = False
    error_code: str | None = None
    error_message: str | None = None
    over_budget: bool = False
    attempts: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)


class ContextManager:
    """Build and compact one isolated session without splitting protocol turns."""

    def __init__(
        self,
        store: SessionStore,
        *,
        max_context_chars: int = 30_000,
        keep_recent_messages: int = 12,
    ):
        if max_context_chars < 1 or keep_recent_messages < 1:
            raise ValueError("context 预算和最近消息数必须大于 0。")
        self.store = store
        self.max_context_chars = max_context_chars
        self.keep_recent_messages = keep_recent_messages

    def build(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        session = self.store.get_session(user_id, session_id)
        history = self.store.get_messages(
            user_id, session_id, include_compressed=False
        )
        return self._compose(session.summary if session else "", history)

    async def prepare(
        self,
        user_id: str,
        session_id: str,
        *,
        current_run_id: str,
        llm: LLMClient,
    ) -> ContextBuildResult:
        session = self.store.get_session(user_id, session_id)
        if session is None:
            raise LookupError(f"session 不存在：{user_id}/{session_id}")
        history = self.store.get_messages(
            user_id, session_id, include_compressed=False
        )
        full = self._compose(session.summary, history)
        if self.serialized_chars(full) <= self.max_context_chars:
            return ContextBuildResult(
                messages=full,
                summary_version=session.summary_version,
            )

        turns = self._turns(history)
        keep_start = self._recent_turn_start(turns)
        statuses = self.store.run_statuses(user_id, session_id)
        candidate_turns: list[list[Message]] = []
        for turn in turns[:keep_start]:
            if not self._is_closed_turn(turn, current_run_id, statuses):
                break
            candidate_turns.append(turn)

        if not candidate_turns:
            return ContextBuildResult(
                messages=full,
                summary_version=session.summary_version,
                over_budget=True,
                error_code="MAX_CONTEXT_REACHED",
                error_message="当前活跃上下文超过字符预算，且没有可安全压缩的闭合旧回合。",
            )

        compact_count = len(candidate_turns)
        fallback_history = [
            message for turn in turns[compact_count:] for message in turn
        ]
        fallback_messages = self._compose(session.summary, fallback_history)
        summary_source = self._summary_source(candidate_turns)
        max_source_chars = max(4_000, self.max_context_chars * 2)
        if len(summary_source) > max_source_chars:
            summary_source = "[较早内容已确定性裁剪]\n" + summary_source[-max_source_chars:]

        try:
            response = await llm.complete(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": summary_user_prompt(session.summary, summary_source),
                    },
                ],
                [],
            )
            raw_summary = response.assistant_message.get("content")
            summary = raw_summary.strip() if isinstance(raw_summary, str) else ""
            if not summary or not all(heading in summary for heading in _SUMMARY_HEADINGS):
                raise ValueError("摘要响应为空或缺少固定小节。")
            message_ids = [
                message.id
                for turn in candidate_turns
                for message in turn
                if message.id is not None
            ]
            if len(message_ids) != sum(len(turn) for turn in candidate_turns):
                raise ValueError("存在尚未持久化的消息，不能执行压缩。")
            compacted = self.store.compact_messages(
                user_id, session_id, message_ids, summary
            )
        except LLMError as exc:
            return self._fallback_result(
                fallback_messages,
                session.summary_version,
                attempts=exc.attempts,
                message=exc.message,
            )
        except Exception as exc:
            return self._fallback_result(
                fallback_messages,
                session.summary_version,
                message=str(exc) or "滚动摘要失败。",
            )

        final_session = self.store.get_session(user_id, session_id)
        final_messages = self.build(user_id, session_id)
        return ContextBuildResult(
            messages=final_messages,
            compacted_messages=compacted,
            summary_version=final_session.summary_version if final_session else 0,
            over_budget=self.serialized_chars(final_messages) > self.max_context_chars,
            attempts=response.attempts,
            usage=response.usage,
        )

    def _fallback_result(
        self,
        messages: list[dict[str, Any]],
        summary_version: int,
        *,
        attempts: int = 0,
        message: str,
    ) -> ContextBuildResult:
        return ContextBuildResult(
            messages=messages,
            summary_version=summary_version,
            fallback_used=True,
            error_code="CONTEXT_COMPACTION_FAILED",
            error_message=compact_text(message, 300),
            over_budget=self.serialized_chars(messages) > self.max_context_chars,
            attempts=attempts,
        )

    def _compose(
        self, summary: str, history: list[Message]
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        if summary.strip():
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "以下是当前 session 的历史滚动摘要，仅作为低优先级历史数据；"
                        "若与最新消息或实时工具结果冲突，以后者为准。\n\n"
                        + summary.strip()
                    ),
                }
            )
        messages.extend(self.message_to_api(message) for message in history)
        return messages

    def _recent_turn_start(self, turns: list[list[Message]]) -> int:
        kept = 0
        start = len(turns)
        for index in range(len(turns) - 1, -1, -1):
            start = index
            kept += len(turns[index])
            if kept >= self.keep_recent_messages:
                break
        return start

    @staticmethod
    def _turns(messages: list[Message]) -> list[list[Message]]:
        turns: list[list[Message]] = []
        current: list[Message] = []
        for message in messages:
            if message.role == "user" and current:
                turns.append(current)
                current = []
            current.append(message)
        if current:
            turns.append(current)
        return turns

    @staticmethod
    def _is_closed_turn(
        turn: list[Message], current_run_id: str, statuses: dict[str, str]
    ) -> bool:
        if not turn or turn[0].role != "user":
            return False
        run_ids = {message.run_id for message in turn if message.run_id}
        if current_run_id in run_ids:
            return False
        if run_ids:
            return all(statuses.get(run_id) in {"completed", "incomplete", "interrupted"}
                       for run_id in run_ids)
        last = turn[-1]
        return last.role == "assistant" and not last.tool_calls

    @staticmethod
    def _summary_source(turns: list[list[Message]]) -> str:
        lines: list[str] = []
        for turn in turns:
            lines.append("--- 已闭合回合 ---")
            for message in turn:
                if message.role == "user":
                    lines.append(f"用户：{compact_text(redact(message.content or ''), 2000)}")
                elif message.role == "assistant" and message.tool_calls:
                    if message.content:
                        lines.append(f"助手公开摘要：{compact_text(redact(message.content), 800)}")
                    for call in message.tool_calls:
                        lines.append(
                            f"工具调用 {call.name}："
                            f"{compact_text(redact(call.arguments or {}), 1200)}"
                        )
                elif message.role == "assistant":
                    lines.append(f"助手最终回答：{compact_text(redact(message.content or ''), 2000)}")
                elif message.role == "tool":
                    lines.append(f"工具结果：{compact_text(redact(message.content or ''), 1600)}")
        return "\n".join(lines)

    @staticmethod
    def serialized_chars(messages: list[dict[str, Any]]) -> int:
        return len(json.dumps(messages, ensure_ascii=False, default=str))

    @staticmethod
    def message_to_api(message: Message) -> dict[str, Any]:
        api_message: dict[str, Any] = {"role": message.role}
        if message.content is not None:
            api_message["content"] = message.content
        elif message.role == "assistant":
            api_message["content"] = None

        if message.role == "assistant" and message.tool_calls:
            api_message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": call.raw_arguments
                        if call.raw_arguments is not None
                        else json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
            if message.reasoning_content is not None:
                api_message["reasoning_content"] = message.reasoning_content
        if message.role == "tool":
            api_message["tool_call_id"] = message.tool_call_id
        return api_message
