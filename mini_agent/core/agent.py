from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from mini_agent.config import Settings
from mini_agent.core.context import ContextManager
from mini_agent.core.experience import ExperienceManager
from mini_agent.core.models import Message, RunState, ToolCall
from mini_agent.core.parser import ParseIssue, ParsedAssistant, ParsedToolCall, parse_response
from mini_agent.core.trace import RunTrace, TraceSink, compact_text, create_run_trace
from mini_agent.llm.base import LLMClient, LLMError, LLMResponse, LLMUsage
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.base import ToolContext, ToolErrorInfo, ToolResult
from mini_agent.tools.registry import ToolRegistry

LOGGER = logging.getLogger(__name__)


RunStatus = Literal["completed", "incomplete", "interrupted"]


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    content: str
    state: RunState
    error_code: str | None = None


class Agent:
    """Explicit model/tool control loop with no third-party agent runner."""

    def __init__(
        self,
        *,
        settings: Settings,
        llm: LLMClient,
        registry: ToolRegistry,
        store: SessionStore,
        context: ContextManager | None = None,
        experience: ExperienceManager | None = None,
        trace_renderer: TraceSink | None = None,
        trace_sinks: tuple[TraceSink, ...] = (),
    ):
        self.settings = settings
        self.llm = llm
        self.registry = registry
        self.store = store
        self.experience = experience or ExperienceManager(store)
        self.context = context or ContextManager(
            store,
            max_context_tokens=settings.max_context_tokens,
            keep_recent_messages=settings.keep_recent_messages,
            experience=self.experience,
        )
        self.trace_renderer = trace_renderer
        self.trace_sinks = trace_sinks

    async def run_turn(self, user_id: str, session_id: str, user_text: str) -> RunResult:
        result = await self._run_turn_core(user_id, session_id, user_text)
        if self.experience is not None:
            try:
                self.experience.write(
                    result.state,
                    status=result.status,
                    error_code=result.error_code,
                    content=result.content,
                )
            except Exception:
                LOGGER.exception("experience 沉淀失败，不影响本次结果")
        return result

    async def _run_turn_core(self, user_id: str, session_id: str, user_text: str) -> RunResult:
        user_text = user_text.strip()
        if not user_text:
            raise ValueError("用户输入不能为空。")

        state = RunState(
            run_id=uuid.uuid4().hex,
            user_id=user_id,
            session_id=session_id,
        )
        trace = create_run_trace(
            self.settings.data_dir,
            state.run_id,
            self.trace_renderer,
            extra_sinks=self.trace_sinks,
        )
        started = time.perf_counter()
        trace.emit(
            "run_start",
            user_id=user_id,
            session_id=session_id,
            model=self.settings.deepseek_model,
        )
        retry_observer = getattr(self.llm, "set_retry_observer", None)
        if callable(retry_observer):
            retry_observer(lambda event: trace.emit("retry", **event))

        run_started = False
        try:
            self.store.create_session(user_id, session_id)
            self.store.start_run(state)
            run_started = True
            self.store.append_message(
                user_id,
                session_id,
                Message(role="user", content=user_text, run_id=state.run_id),
            )
        except sqlite3.Error:
            return self._finish_storage_failure(
                state, trace, started, run_started=run_started
            )
        try:
            context_result = await self.context.prepare(
                user_id,
                session_id,
                current_run_id=state.run_id,
                llm=self.llm,
            )
            active_messages = context_result.messages
            state.api_attempts += context_result.attempts
            if context_result.compacted_messages:
                state.successful_llm_calls += 1
                self._accumulate_usage(state, context_result.usage)
                trace.emit(
                    "context_compacted",
                    compacted_messages=context_result.compacted_messages,
                    summary_version=context_result.summary_version,
                    message=(
                        f"上下文已压缩 {context_result.compacted_messages} 条旧消息，"
                        f"摘要版本 v{context_result.summary_version}。"
                    ),
                )
            if context_result.error_code:
                trace.emit(
                    "error",
                    code=context_result.error_code,
                    message=context_result.error_message,
                    fallback_used=context_result.fallback_used,
                )
            trace.emit(
                "context_built",
                message_count=len(active_messages),
                serialized_tokens=self.context.serialized_tokens(active_messages),
                max_tokens=self.settings.max_context_tokens,
                fallback_used=context_result.fallback_used,
                summary_version=context_result.summary_version,
            )
            if context_result.summary_quality_warning:
                trace.emit(
                    "context_summary_quality",
                    code="SUMMARY_QUALITY_LOW",
                    message=context_result.summary_quality_warning,
                )
            if context_result.over_budget:
                return self._finish_incomplete(
                    state,
                    "MAX_CONTEXT_REACHED",
                    "当前活跃上下文超过字符预算，不能安全拆分工具链。",
                    trace,
                    started,
                )
            if self._token_budget_reached(state):
                trace.emit(
                    "budget_warning",
                    code="MAX_TOTAL_TOKENS_REACHED",
                    message="上下文摘要已用尽本轮 token 预算。",
                )
                return self._finish_incomplete(
                    state,
                    "MAX_TOTAL_TOKENS_REACHED",
                    "模型 token 使用量达到本轮上限。",
                    trace,
                    started,
                )
            return await self._run_loop(state, active_messages, trace, started)
        except asyncio.CancelledError:
            # Preserve completed messages/tool results and close the durable run.
            return self._finish_interrupted(state, trace, started)
        except sqlite3.Error:
            return self._finish_storage_failure(
                state, trace, started, run_started=run_started
            )

    async def _run_loop(
        self,
        state: RunState,
        active_messages: list[dict[str, Any]],
        trace: RunTrace,
        started: float,
    ) -> RunResult:
        protocol_errors = 0
        consecutive_tool_errors = 0
        signature_counts: dict[str, int] = {}
        run_retries = 0

        while state.round < self.settings.max_llm_rounds_per_turn:
            if self._token_budget_reached(state):
                return self._finish_incomplete(
                    state,
                    "MAX_TOTAL_TOKENS_REACHED",
                    "模型 token 使用量达到本轮上限。",
                    trace,
                    started,
                )
            state.round += 1
            llm_started = time.perf_counter()
            trace.emit("llm_call_start", round=state.round)
            try:
                response = await self.llm.complete(
                    active_messages,
                    self.registry.as_llm_tools(),
                    max_output_tokens=self._remaining_output_budget(state),
                )
            except LLMError as exc:
                state.api_attempts += exc.attempts
                trace.emit(
                    "llm_call_end",
                    round=state.round,
                    ok=False,
                    attempts=exc.attempts,
                    error_code=exc.code,
                    duration_ms=self._elapsed_ms(llm_started),
                )
                if (
                    exc.retryable
                    and run_retries == 0
                    and state.round == 1
                    and state.tool_step == 0
                ):
                    # Run-level safety net: retry once when the very first model
                    # call fails before any side-effect tool has run.
                    run_retries += 1
                    state.round -= 1
                    trace.emit(
                        "retry",
                        round=1,
                        code=exc.code,
                        scope="run_retry_once",
                        message=f"首次模型调用瞬时失败（{exc.code}），已自动重试一次。",
                    )
                    continue
                return self._finish_incomplete(
                    state, exc.code, exc.message, trace, started
                )

            self._update_usage(state, response)
            trace.emit(
                "llm_call_end",
                round=state.round,
                ok=True,
                model=response.model,
                finish_reason=response.finish_reason,
                attempts=response.attempts,
                duration_ms=self._elapsed_ms(llm_started),
                usage={
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                has_content=bool(response.assistant_message.get("content")),
                tool_call_count=len(response.assistant_message.get("tool_calls") or []),
            )
            decision = parse_response(response)
            trace.emit(
                "assistant_decision",
                round=state.round,
                kind=decision.kind,
                finish_reason=decision.finish_reason,
                public_summary=decision.content if decision.kind == "tool_calls" else None,
                tools=[call.name for call in decision.tool_calls],
            )

            if decision.kind == "final":
                assert decision.content is not None
                self.store.append_message(
                    state.user_id,
                    state.session_id,
                    Message(
                        role="assistant",
                        content=decision.content,
                        reasoning_content=decision.reasoning_content,
                        run_id=state.run_id,
                    ),
                )
                state.status = "completed"
                self.store.finish_run(state)
                self._emit_run_end(state, trace, started)
                return RunResult("completed", decision.content, state)

            if self._token_budget_reached(state):
                trace.emit(
                    "budget_warning",
                    round=state.round,
                    code="MAX_TOTAL_TOKENS_REACHED",
                    message="本轮 token 预算已用尽，停止后续工具和模型调用。",
                )
                return self._finish_incomplete(
                    state,
                    "MAX_TOTAL_TOKENS_REACHED",
                    "模型 token 使用量达到本轮上限。",
                    trace,
                    started,
                )

            if decision.kind == "retry":
                protocol_errors += 1
                issue = decision.issue or ParseIssue("LLM_SERVER_ERROR", "模型暂时不可用。")
                trace.emit(
                    "retry",
                    round=state.round,
                    code=issue.code,
                    attempt=protocol_errors,
                    message=issue.message,
                )
                if protocol_errors >= self.settings.max_protocol_errors:
                    return self._finish_incomplete(
                        state, issue.code, issue.message, trace, started
                    )
                continue

            if decision.kind == "invalid":
                protocol_errors += 1
                issue = decision.issue or ParseIssue("EMPTY_MODEL_RESPONSE", "模型响应无效。")
                trace.emit(
                    "retry",
                    round=state.round,
                    code=issue.code,
                    attempt=protocol_errors,
                    message=issue.message,
                )
                if protocol_errors >= self.settings.max_protocol_errors:
                    return self._finish_incomplete(
                        state, issue.code, issue.message, trace, started
                    )
                active_messages.append(
                    {
                        "role": "system",
                        "content": (
                            f"上一条响应无效（{issue.code}）：{issue.message}"
                            "请重新给出非空最终回答，或给出完整、合法的工具调用。"
                        ),
                    }
                )
                continue

            protocol_errors = 0
            if state.tool_step + len(decision.tool_calls) > self.settings.max_tool_calls_per_turn:
                return self._finish_incomplete(
                    state,
                    "MAX_TOOL_CALLS_REACHED",
                    "工具调用数量达到本轮上限。",
                    trace,
                    started,
                )

            stored_calls = [self._stored_call(call) for call in decision.tool_calls]
            self.store.append_message(
                state.user_id,
                state.session_id,
                Message(
                    role="assistant",
                    content=decision.content,
                    tool_calls=stored_calls,
                    reasoning_content=decision.reasoning_content,
                    run_id=state.run_id,
                ),
            )
            active_messages.append(self._assistant_message(response, decision))

            for parsed_call in decision.tool_calls:
                args = parsed_call.call.arguments if parsed_call.call is not None else {}
                trace.emit(
                    "tool_call_start",
                    round=state.round,
                    step=state.tool_step + 1,
                    tool=parsed_call.name,
                    tool_call_id=parsed_call.id,
                    args=args,
                    validation_error=(
                        parsed_call.issue.code if parsed_call.issue is not None else None
                    ),
                )
                result = await self._invoke(parsed_call, state)
                state.tool_step += 1
                if result.ok:
                    consecutive_tool_errors = 0
                else:
                    consecutive_tool_errors += 1
                state.consecutive_errors = consecutive_tool_errors

                result_summary = (
                    compact_text(result.data)
                    if result.ok
                    else compact_text(result.error.message if result.error else "工具执行失败")
                )
                trace.emit(
                    "tool_call_end",
                    round=state.round,
                    step=state.tool_step,
                    tool=parsed_call.name,
                    tool_call_id=parsed_call.id,
                    args=args,
                    ok=result.ok,
                    result=result.as_dict(),
                    result_summary=result_summary,
                    error_code=result.error.code if result.error else None,
                    duration_ms=result.meta.get("duration_ms", 0),
                )

                tool_content = json.dumps(result.as_dict(), ensure_ascii=False, default=str)
                tool_message = {
                    "role": "tool",
                    "tool_call_id": parsed_call.id,
                    "content": tool_content,
                }
                self.store.append_message(
                    state.user_id,
                    state.session_id,
                    Message(
                        role="tool",
                        content=tool_content,
                        tool_call_id=parsed_call.id,
                        run_id=state.run_id,
                    ),
                )
                active_messages.append(tool_message)

                signature = self._tool_result_signature(parsed_call, result)
                signature_counts[signature] = signature_counts.get(signature, 0) + 1
                state.repeated_call_count = max(
                    state.repeated_call_count, signature_counts[signature]
                )
                if signature_counts[signature] >= self.settings.max_repeated_calls:
                    trace.emit(
                        "budget_warning",
                        round=state.round,
                        code="NO_PROGRESS",
                        signature=signature,
                        repeats=signature_counts[signature],
                        message="检测到相同工具、参数和结果重复，已停止无进展循环。",
                    )
                    return self._finish_incomplete(
                        state,
                        "NO_PROGRESS",
                        "检测到相同工具调用和结果重复，任务没有取得进展。",
                        trace,
                        started,
                    )
                if consecutive_tool_errors >= self.settings.max_consecutive_tool_errors:
                    return self._finish_incomplete(
                        state,
                        "TOOL_EXECUTION_FAILED",
                        "工具连续失败次数达到本轮上限。",
                        trace,
                        started,
                    )

        return self._finish_incomplete(
            state,
            "MAX_ROUNDS_REACHED",
            "模型调用轮次达到本轮上限。",
            trace,
            started,
        )

    async def _invoke(self, parsed_call: ParsedToolCall, state: RunState) -> ToolResult:
        if parsed_call.id in state.seen_tool_call_ids:
            return ToolResult(
                ok=False,
                tool=parsed_call.name,
                error=ToolErrorInfo(
                    "DUPLICATE_TOOL_CALL_ID", "同一 run 中出现重复 tool_call_id。", False
                ),
            )
        state.seen_tool_call_ids.add(parsed_call.id)
        if parsed_call.issue is not None:
            return ToolResult(
                ok=False,
                tool=parsed_call.name,
                error=ToolErrorInfo(
                    parsed_call.issue.code, parsed_call.issue.message, False
                ),
            )
        assert parsed_call.call is not None
        return await self.registry.invoke(
            parsed_call.call,
            ToolContext(
                user_id=state.user_id,
                session_id=state.session_id,
                store=self.store,
                run_id=state.run_id,
            ),
        )

    @staticmethod
    def _stored_call(parsed_call: ParsedToolCall) -> ToolCall:
        arguments = parsed_call.call.arguments if parsed_call.call is not None else None
        return ToolCall(
            id=parsed_call.id,
            name=parsed_call.name,
            arguments=arguments,
            raw_arguments=parsed_call.raw_arguments,
        )

    @staticmethod
    def _assistant_message(
        response: LLMResponse, decision: ParsedAssistant
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "role": "assistant",
            "content": decision.content,
            "tool_calls": response.assistant_message["tool_calls"],
        }
        if decision.reasoning_content is not None:
            message["reasoning_content"] = decision.reasoning_content
        return message

    def _update_usage(self, state: RunState, response: LLMResponse) -> None:
        state.api_attempts += response.attempts
        state.successful_llm_calls += 1
        self._accumulate_usage(state, response.usage)

    def _accumulate_usage(self, state: RunState, usage: LLMUsage) -> None:
        state.prompt_tokens += usage.prompt_tokens
        state.completion_tokens += usage.completion_tokens
        state.total_tokens += usage.total_tokens
        hit = usage.prompt_cache_hit_tokens or 0
        miss = usage.prompt_cache_miss_tokens or 0
        if hit + miss == 0:
            # DeepSeek 通常返回缓存命中/未命中字段；缺失时按未命中保守估算。
            miss = usage.prompt_tokens
        state.cost_usd += (
            miss * self.settings.deepseek_price_input_per_1m
            + hit * self.settings.deepseek_price_input_cache_hit_per_1m
            + usage.completion_tokens * self.settings.deepseek_price_output_per_1m
        ) / 1_000_000

    def _token_budget_reached(self, state: RunState) -> bool:
        limit = self.settings.max_total_tokens_per_turn
        return limit is not None and state.total_tokens >= limit

    def _remaining_output_budget(self, state: RunState) -> int | None:
        """Streaming hint: how many output tokens this call may still spend."""
        limit = self.settings.max_total_tokens_per_turn
        if limit is None:
            return None
        return max(1, limit - state.total_tokens)

    @staticmethod
    def _tool_result_signature(
        parsed_call: ParsedToolCall, result: ToolResult
    ) -> str:
        payload = {
            "tool": parsed_call.name,
            "arguments": parsed_call.call.arguments if parsed_call.call else None,
            "result": {
                "ok": result.ok,
                "data": result.data,
                "error": result.error.as_dict() if result.error else None,
                "truncated": bool(result.meta.get("truncated")),
            },
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def _finish_incomplete(
        self,
        state: RunState,
        code: str,
        message: str,
        trace: RunTrace,
        started: float,
    ) -> RunResult:
        state.status = "incomplete"
        content = (
            f"本次任务未完整完成：{message} "
            f"已进行 {state.round} 轮模型调用、{state.tool_step} 次工具调用；"
            "当前 session 数据已保留，可以继续重试。"
        )
        self.store.append_message(
            state.user_id,
            state.session_id,
            Message(role="assistant", content=content, run_id=state.run_id),
        )
        self.store.finish_run(state, error=code)
        trace.emit("error", code=code, message=message, round=state.round)
        self._emit_run_end(state, trace, started, error_code=code)
        return RunResult("incomplete", content, state, error_code=code)

    def _finish_interrupted(
        self, state: RunState, trace: RunTrace, started: float
    ) -> RunResult:
        state.status = "interrupted"
        content = (
            "本次任务已中断；已经完成的消息和工具结果均已保留，"
            "可以在当前 session 中继续。可输入“继续”或重述目标来恢复任务。"
        )
        self.store.finish_run(state, error="INTERRUPTED")
        trace.emit("error", code="INTERRUPTED", message="用户中断了当前任务。")
        self._emit_run_end(state, trace, started, error_code="INTERRUPTED")
        return RunResult("interrupted", content, state, error_code="INTERRUPTED")

    def _finish_storage_failure(
        self,
        state: RunState,
        trace: RunTrace,
        started: float,
        *,
        run_started: bool,
    ) -> RunResult:
        state.status = "incomplete"
        content = (
            "本次任务未完整完成：本地 session 存储失败，无法保证状态一致性。"
            "请检查数据目录和数据库占用后重试。"
        )
        if run_started:
            try:
                self.store.finish_run(state, error="SESSION_STORE_FAILED")
            except sqlite3.Error:
                pass
        trace.emit(
            "error",
            code="SESSION_STORE_FAILED",
            message="本地 session 存储失败，当前 run 已停止。",
        )
        self._emit_run_end(
            state, trace, started, error_code="SESSION_STORE_FAILED"
        )
        return RunResult(
            "incomplete", content, state, error_code="SESSION_STORE_FAILED"
        )

    @classmethod
    def _emit_run_end(
        cls,
        state: RunState,
        trace: RunTrace,
        started: float,
        *,
        error_code: str | None = None,
    ) -> None:
        trace.emit(
            "run_end",
            status=state.status,
            rounds=state.round,
            tools=state.tool_step,
            api_attempts=state.api_attempts,
            successful_llm_calls=state.successful_llm_calls,
            tokens=state.total_tokens,
            prompt_tokens=state.prompt_tokens,
            completion_tokens=state.completion_tokens,
            cost_usd=round(state.cost_usd, 6),
            duration_ms=cls._elapsed_ms(started),
            error_code=error_code,
        )

    @staticmethod
    def _elapsed_ms(started: float) -> int:
        return max(0, round((time.perf_counter() - started) * 1000))
