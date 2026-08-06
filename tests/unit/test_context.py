from __future__ import annotations

import asyncio

from mini_agent.core.context import ContextManager
from mini_agent.core.models import Message, RunState, ToolCall
from mini_agent.llm.base import LLMError, LLMResponse, LLMUsage
from mini_agent.sessions.store import SessionStore


SUMMARY = """## 已确认事实
- 用户在准备笔试
## 用户偏好
- 回答简洁
## 工具结果
- 计算结果为 42
## 未解决事项
- 完成提交"""


class SummaryLLM:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append((messages, tools))
        if self.fail:
            raise LLMError(
                "LLM_TIMEOUT", "summary timeout", retryable=True, attempts=2
            )
        return LLMResponse(
            "summary-id",
            "fake",
            {"role": "assistant", "content": SUMMARY},
            "stop",
            LLMUsage(total_tokens=9),
            attempts=1,
        )


def add_closed_turn(
    store: SessionStore,
    run_id: str,
    user_text: str,
    final_text: str,
    *,
    tool_chain: bool = False,
) -> list[int]:
    state = RunState(run_id, "user", "session")
    store.start_run(state)
    ids = [
        store.append_message(
            "user", "session", Message(role="user", content=user_text, run_id=run_id)
        )
    ]
    if tool_chain:
        ids.append(
            store.append_message(
                "user",
                "session",
                Message(
                    role="assistant",
                    content="调用计算器",
                    tool_calls=[
                        ToolCall("call-1", "calculator", {"expression": "6*7"})
                    ],
                    reasoning_content="private-reasoning-must-not-be-summarized",
                    run_id=run_id,
                ),
            )
        )
        ids.append(
            store.append_message(
                "user",
                "session",
                Message(
                    role="tool",
                    content='{"ok":true,"data":{"value":42}}',
                    tool_call_id="call-1",
                    run_id=run_id,
                ),
            )
        )
    ids.append(
        store.append_message(
            "user",
            "session",
            Message(role="assistant", content=final_text, run_id=run_id),
        )
    )
    state.status = "completed"
    store.finish_run(state)
    return ids


def start_current_turn(store: SessionStore, run_id: str, text: str) -> None:
    state = RunState(run_id, "user", "session")
    store.start_run(state)
    store.append_message(
        "user", "session", Message(role="user", content=text, run_id=run_id)
    )


def test_context_compacts_whole_closed_tool_chain_and_keeps_recent(tmp_path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    old_ids = add_closed_turn(
        store,
        "old-1",
        "我在准备笔试；临时密钥 sk-abcdefghijklmnop " + "旧内容" * 120,
        "结果是 42",
        tool_chain=True,
    )
    add_closed_turn(store, "recent-1", "请记住偏好" + "新内容" * 80, "回答简洁")
    start_current_turn(store, "current", "继续当前任务")
    llm = SummaryLLM()
    manager = ContextManager(store, max_context_tokens=500, keep_recent_messages=3)

    result = asyncio.run(
        manager.prepare(
            "user", "session", current_run_id="current", llm=llm
        )
    )

    assert result.compacted_messages == len(old_ids)
    assert result.summary_version == 1
    assert result.messages[1]["role"] == "system"
    assert "## 已确认事实" in result.messages[1]["content"]
    assert result.messages[-1]["content"] == "继续当前任务"
    by_id = {message.id: message for message in store.get_messages("user", "session")}
    assert all(by_id[message_id].is_compressed for message_id in old_ids)
    summary_request = llm.requests[0][0][1]["content"]
    assert "private-reasoning-must-not-be-summarized" not in summary_request
    assert "abcdefghijklmnop" not in summary_request
    assert "trace.jsonl" not in summary_request


def test_context_summary_failure_keeps_database_and_uses_safe_fallback(tmp_path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    old_ids = add_closed_turn(
        store, "old-1", "很长的旧事实" * 120, "旧回答", tool_chain=True
    )
    add_closed_turn(store, "recent-1", "最近事实" * 80, "最近回答")
    start_current_turn(store, "current", "当前问题")
    manager = ContextManager(store, max_context_tokens=500, keep_recent_messages=3)

    result = asyncio.run(
        manager.prepare(
            "user", "session", current_run_id="current", llm=SummaryLLM(fail=True)
        )
    )

    assert result.fallback_used
    assert result.error_code == "CONTEXT_COMPACTION_FAILED"
    assert result.attempts == 2
    assert result.messages[-1]["content"] == "当前问题"
    session = store.get_session("user", "session")
    assert session is not None and session.summary_version == 0
    by_id = {message.id: message for message in store.get_messages("user", "session")}
    assert all(not by_id[message_id].is_compressed for message_id in old_ids)


def test_context_below_budget_does_not_call_summary_llm(tmp_path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    start_current_turn(store, "current", "短消息")
    llm = SummaryLLM()
    manager = ContextManager(store, max_context_tokens=2_000, keep_recent_messages=2)

    result = asyncio.run(
        manager.prepare(
            "user", "session", current_run_id="current", llm=llm
        )
    )

    assert result.compacted_messages == 0
    assert not result.over_budget
    assert llm.requests == []


def test_summary_quality_warning_flags_fact_loss(tmp_path) -> None:
    from mini_agent.core.context import summary_quality_warning

    assert summary_quality_warning("苹果 苹果 苹果 香蕉", "苹果") is None  # 保留 1/2
    warning = summary_quality_warning("苹果 苹果 苹果 香蕉 香蕉", "与事实无关的摘要")
    assert warning is not None
    assert "高频词" in warning


def test_compact_result_carries_summary_quality_warning(tmp_path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    add_closed_turn(store, "old-1", "旧内容" * 120, "结果是 42", tool_chain=True)
    add_closed_turn(store, "recent-1", "最近事实" * 80, "最近回答")
    start_current_turn(store, "current", "当前问题")
    manager = ContextManager(store, max_context_tokens=500, keep_recent_messages=3)

    result = asyncio.run(
        manager.prepare(
            "user", "session", current_run_id="current", llm=SummaryLLM()
        )
    )

    # 摘要 SUMMARY 不含高频词“旧内容”，应产生质量告警但不阻断
    assert result.compacted_messages > 0
    assert result.summary_quality_warning is not None
