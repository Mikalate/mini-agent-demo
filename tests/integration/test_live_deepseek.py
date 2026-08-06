from __future__ import annotations

import asyncio
import copy
import json
import os

import pytest

from mini_agent.config import Settings
from mini_agent.core.agent import Agent
from mini_agent.llm.deepseek import DeepSeekClient
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.environ.get("DEEPSEEK_API_KEY"),
        reason="DEEPSEEK_API_KEY is required for live checks",
    ),
]


class RecordingLLM:
    def __init__(self, inner):
        self.inner = inner
        self.requests = []

    def set_retry_observer(self, observer):
        setter = getattr(self.inner, "set_retry_observer", None)
        if callable(setter):
            setter(observer)

    async def complete(self, messages, tools, **kwargs):
        self.requests.append(copy.deepcopy(messages))
        return await self.inner.complete(messages, tools, **kwargs)


def live_runtime(tmp_path, monkeypatch, *, data_name="live-agent-data"):
    monkeypatch.setenv("AGENT_DATA_DIR", str(tmp_path / data_name))
    monkeypatch.setenv("DEEPSEEK_THINKING", "enabled")
    settings = Settings.from_env(tmp_path / "missing.env")
    store = SessionStore(settings.database_path)
    llm = RecordingLLM(DeepSeekClient(settings))
    agent = Agent(
        settings=settings,
        llm=llm,
        registry=build_default_registry(),
        store=store,
    )
    return settings, store, llm, agent


def tool_names(store, user_id, session_id):
    return [
        call.name
        for message in store.get_messages(user_id, session_id)
        for call in message.tool_calls
    ]


def test_real_direct_answer_can_finish_without_tool(tmp_path, monkeypatch) -> None:
    _, store, _, agent = live_runtime(tmp_path, monkeypatch)

    result = asyncio.run(
        agent.run_turn(
            "live_user", "direct", "这是普通知识问答，请不要调用工具：中国的首都是哪里？"
        )
    )

    assert result.status == "completed"
    assert "北京" in result.content
    assert tool_names(store, "live_user", "direct") == []


def test_real_calculator_loop_and_reasoning_round_trip(tmp_path, monkeypatch) -> None:
    _, store, llm, agent = live_runtime(tmp_path, monkeypatch)

    result = asyncio.run(
        agent.run_turn(
            "live_user",
            "calculator",
            "请务必使用 calculator 工具计算 18*24+7，再按工具真实结果回答。",
        )
    )

    assert result.status == "completed"
    assert "439" in result.content
    assert "calculator" in tool_names(store, "live_user", "calculator")
    history = store.get_messages("live_user", "calculator")
    first_tool_assistant = next(message for message in history if message.tool_calls)
    assert len(llm.requests) >= 2
    round_tripped = next(
        message for message in llm.requests[1] if message.get("tool_calls")
    )
    assert round_tripped.get("reasoning_content") == first_tool_assistant.reasoning_content


def test_real_local_search_is_used_and_summarized(tmp_path, monkeypatch) -> None:
    _, store, _, agent = live_runtime(tmp_path, monkeypatch)

    result = asyncio.run(
        agent.run_turn(
            "live_user",
            "search",
            "请调用 search 搜索本地语料中关于 context compression 的内容并概括。",
        )
    )

    assert result.status == "completed"
    assert "search" in tool_names(store, "live_user", "search")
    assert any(word in result.content.lower() for word in ["摘要", "最近", "summary"])


def test_real_weather_todo_two_sessions_and_recovery(tmp_path, monkeypatch) -> None:
    settings, store, _, agent = live_runtime(tmp_path, monkeypatch)

    weather_todo = asyncio.run(
        agent.run_turn(
            "live_user",
            "window_1",
            "先调用 weather 查询上海天气；如果工具建议带伞，再调用 todo 添加“明天出门带伞”，最后回答。",
        )
    )
    other_session = asyncio.run(
        agent.run_turn(
            "live_user",
            "window_2",
            "先调用 todo 添加“明天交笔试”，然后再次调用 todo 列出当前待办，最后回答。",
        )
    )

    names_1 = tool_names(store, "live_user", "window_1")
    names_2 = tool_names(store, "live_user", "window_2")
    assert weather_todo.status == other_session.status == "completed"
    assert "weather" in names_1 and "todo" in names_1
    assert names_2.count("todo") >= 2
    assert [todo.text for todo in store.list_todos("live_user", "window_1")] == [
        "明天出门带伞"
    ]
    assert [todo.text for todo in store.list_todos("live_user", "window_2")] == [
        "明天交笔试"
    ]

    reopened_store = SessionStore(settings.database_path)
    recovery_llm = RecordingLLM(DeepSeekClient(settings))
    recovered_agent = Agent(
        settings=settings,
        llm=recovery_llm,
        registry=build_default_registry(),
        store=reopened_store,
    )
    recovered = asyncio.run(
        recovered_agent.run_turn(
            "live_user",
            "window_1",
            "只根据当前 session 历史简短回答，不要调用工具：刚才为什么建议带伞？",
        )
    )
    first_request = json.dumps(recovery_llm.requests[0], ensure_ascii=False, default=str)
    assert recovered.status == "completed"
    assert "上海" in first_request and "带伞" in first_request
    assert "明天交笔试" not in first_request

    for result in (weather_todo, other_session, recovered):
        trace_path = settings.data_dir / "runs" / result.state.run_id / "trace.jsonl"
        raw_trace = trace_path.read_text(encoding="utf-8")
        assert settings.deepseek_api_key not in raw_trace
        history = reopened_store.get_messages(result.state.user_id, result.state.session_id)
        private_values = [
            message.reasoning_content
            for message in history
            if message.reasoning_content
        ]
        assert all(value not in raw_trace for value in private_values)
