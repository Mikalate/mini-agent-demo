import asyncio
import json
from pathlib import Path

from mini_agent.core.agent import Agent
from mini_agent.core.experience import ExperienceManager
from mini_agent.core.models import ExperienceRecord, Message, RunState, ToolCall
from mini_agent.llm.base import LLMResponse
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.registry import build_default_registry


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    async def complete(self, messages, tools, **kwargs):
        self.requests.append(messages)
        return self.responses.pop(0)


def make_store(tmp_path: Path) -> SessionStore:
    return SessionStore(tmp_path / "agent.db")


def seed_failure_run(store: SessionStore, error_code: str) -> None:
    state = RunState("run-fail", "user", "session")
    store.create_session("user", "session")
    store.start_run(state)
    state.status = "incomplete"
    store.finish_run(state, error=error_code)


def test_seeds_are_loaded_once(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ExperienceManager(store)
    assert store.count_experiences() == 5
    ExperienceManager(store)  # 幂等
    assert store.count_experiences() == 5


def test_upsert_deduplicates_by_kind_and_trigger(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    ExperienceManager(store)  # 先加载 5 条种子
    store.upsert_experience(
        ExperienceRecord(id="a", kind="error", trigger="err:TEST", content="旧")
    )
    store.upsert_experience(
        ExperienceRecord(id="b", kind="error", trigger="err:TEST", content="新")
    )
    assert store.count_experiences() == 6  # 5 种子 + 1（合并）
    fetched = store.get_experiences(["err:TEST"], limit=1)
    assert len(fetched) == 1
    assert fetched[0].content == "新"


def test_write_distills_lesson_from_completed_tool_sequence(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manager = ExperienceManager(store)
    state = RunState("run-ok", "user", "session")
    store.create_session("user", "session")
    store.start_run(state)
    store.append_message(
        "user",
        "session",
        Message(
            role="assistant",
            content="调用工具",
            tool_calls=[ToolCall("c1", "weather", {"city": "上海"})],
            run_id=state.run_id,
        ),
    )
    store.append_message(
        "user",
        "session",
        Message(role="tool", content="{}", tool_call_id="c1", run_id=state.run_id),
    )
    state.status = "completed"
    store.finish_run(state)

    manager.write(state, status="completed")

    lessons = store.get_experiences(["seq:weather"], limit=1)
    assert lessons and lessons[0].kind == "lesson"
    assert "weather" in lessons[0].content


def test_write_distills_error_from_incomplete_run(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manager = ExperienceManager(store)
    state = RunState("run-bad", "user", "session")
    store.create_session("user", "session")
    store.start_run(state)
    state.status = "incomplete"
    store.finish_run(state, error="NO_PROGRESS")

    manager.write(
        state, status="incomplete", error_code="NO_PROGRESS", content="没有进展"
    )

    errors = store.get_experiences(["err:NO_PROGRESS"], limit=1)
    assert errors and errors[0].kind == "error"
    assert "NO_PROGRESS" in errors[0].content


def test_read_recalls_error_experience_and_bumps_hit_count(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manager = ExperienceManager(store)
    seed_failure_run(store, "WEATHER_NO_DATA")

    records = manager.read("user", "session")

    assert any(record.trigger == "err:WEATHER_NO_DATA" for record in records)
    fetched = store.get_experiences(["err:WEATHER_NO_DATA"], limit=1)
    assert fetched[0].hit_count >= 1


def test_agent_injects_matched_experience_into_context(settings, tmp_path: Path) -> None:
    store = make_store(tmp_path)
    manager = ExperienceManager(store)
    seed_failure_run(store, "WEATHER_NO_DATA")
    llm = FakeLLM(
        [LLMResponse("id", "fake", {"role": "assistant", "content": "完成"}, "stop")]
    )
    agent = Agent(
        settings=settings,
        llm=llm,
        registry=build_default_registry(),
        store=store,
        experience=manager,
    )

    result = asyncio.run(agent.run_turn("user", "session", "继续任务"))

    assert result.status == "completed"
    serialized = json.dumps(llm.requests[0], ensure_ascii=False)
    assert "历史经验" in serialized
    assert "上海" in serialized  # 种子中的 weather 经验内容


def test_extract_errors_mines_trace_files(tmp_path: Path) -> None:
    from scripts.extract_experiences import extract_errors

    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    lines = [
        '{"event": "tool_call_end", "tool": "weather", "error_code": "WEATHER_NO_DATA"}',
        '{"event": "tool_call_end", "tool": "weather", "error_code": "WEATHER_NO_DATA"}',
        '{"event": "run_end", "status": "incomplete", "error_code": "NO_PROGRESS"}',
        '{"event": "run_end", "status": "interrupted", "error_code": "INTERRUPTED"}',
    ]
    (run_dir / "trace.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    errors = extract_errors(tmp_path / "runs")

    triggers = {item["trigger"] for item in errors}
    assert "err:WEATHER_NO_DATA" in triggers
    assert "err:NO_PROGRESS" in triggers
    assert "err:INTERRUPTED" not in triggers  # 中断不作为经验
    assert len(errors) == 2  # 去重
