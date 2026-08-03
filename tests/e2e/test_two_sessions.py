from __future__ import annotations

import asyncio
import copy
import json

from mini_agent.core.agent import Agent
from mini_agent.llm.base import LLMResponse
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry


def final(text: str) -> LLMResponse:
    return LLMResponse("id", "fake", {"role": "assistant", "content": text}, "stop")


def call(call_id: str, name: str, arguments: dict) -> LLMResponse:
    return LLMResponse(
        "id",
        "fake",
        {
            "role": "assistant",
            "content": f"调用 {name}",
            "reasoning_content": "private-e2e-reasoning",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments, ensure_ascii=False),
                    },
                }
            ],
        },
        "tool_calls",
    )


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, messages, tools):
        self.requests.append(copy.deepcopy(messages))
        return self.responses.pop(0)


def agent(settings, store, llm):
    return Agent(
        settings=settings,
        llm=llm,
        registry=build_default_registry(),
        store=store,
    )


def test_two_sessions_are_isolated_concurrent_and_recoverable(settings, tmp_path) -> None:
    store = SessionStore(settings.database_path)
    window_1_llm = ScriptedLLM(
        [
            call("weather-1", "weather", {"city": "上海"}),
            call("todo-1", "todo", {"action": "add", "text": "明天出门带伞"}),
            final("上海有阵雨，已添加带伞待办。"),
        ]
    )
    window_2_llm = ScriptedLLM(
        [
            call("todo-2", "todo", {"action": "add", "text": "发送周报"}),
            final("已添加发送周报待办。"),
        ]
    )

    async def first_turns():
        return await asyncio.gather(
            agent(settings, store, window_1_llm).run_turn(
                "user_a", "window_1", "查上海天气，下雨就添加带伞待办"
            ),
            agent(settings, store, window_2_llm).run_turn(
                "user_a", "window_2", "添加发送周报待办"
            ),
        )

    result_1, result_2 = asyncio.run(first_turns())
    assert result_1.status == result_2.status == "completed"
    assert [todo.text for todo in store.list_todos("user_a", "window_1")] == [
        "明天出门带伞"
    ]
    assert [todo.text for todo in store.list_todos("user_a", "window_2")] == [
        "发送周报"
    ]

    list_1_llm = ScriptedLLM(
        [call("list-1", "todo", {"action": "list"}), final("待办：明天出门带伞")]
    )
    list_2_llm = ScriptedLLM(
        [call("list-2", "todo", {"action": "list"}), final("待办：发送周报")]
    )

    async def list_turns():
        return await asyncio.gather(
            agent(settings, store, list_1_llm).run_turn(
                "user_a", "window_1", "我的待办有哪些"
            ),
            agent(settings, store, list_2_llm).run_turn(
                "user_a", "window_2", "我的待办有哪些"
            ),
        )

    listed_1, listed_2 = asyncio.run(list_turns())
    assert "带伞" in listed_1.content and "周报" not in listed_1.content
    assert "周报" in listed_2.content and "带伞" not in listed_2.content

    reopened = SessionStore(settings.database_path)
    recovery_llm = ScriptedLLM([final("因为之前查到上海有阵雨，所以建议带伞。")])
    recovered = asyncio.run(
        agent(settings, reopened, recovery_llm).run_turn(
            "user_a", "window_1", "刚才为什么建议带伞"
        )
    )
    assert recovered.status == "completed"
    serialized_request = json.dumps(
        recovery_llm.requests[0], ensure_ascii=False, default=str
    )
    assert "上海" in serialized_request and "阵雨" in serialized_request
    assert "发送周报" not in serialized_request

    for run_id in (result_1.state.run_id, result_2.state.run_id):
        trace_path = settings.data_dir / "runs" / run_id / "trace.jsonl"
        trace = trace_path.read_text(encoding="utf-8")
        events = [json.loads(line) for line in trace.splitlines()]
        event_names = [event["event"] for event in events]
        assert event_names[0] == "run_start"
        assert "context_built" in event_names
        assert "assistant_decision" in event_names
        assert "tool_call_start" in event_names
        assert "tool_call_end" in event_names
        assert event_names[-1] == "run_end"
        assert '"event": "run_end"' in trace
        assert "private-e2e-reasoning" not in trace
        assert settings.deepseek_api_key not in trace
