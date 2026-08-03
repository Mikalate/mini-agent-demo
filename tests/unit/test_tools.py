import asyncio
from pathlib import Path

from mini_agent.core.models import ToolCall
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.base import ToolContext, ToolResult, ToolSpec
from mini_agent.tools.registry import (
    ToolRegistrationError,
    ToolRegistry,
    build_default_registry,
)


def invoke(registry: ToolRegistry, call: ToolCall, context: ToolContext) -> ToolResult:
    return asyncio.run(registry.invoke(call, context))


def test_calculator_returns_42_and_rejects_code(tmp_path: Path) -> None:
    registry = build_default_registry()
    context = ToolContext("user_a", "one", SessionStore(tmp_path / "agent.db"))

    success = invoke(
        registry,
        ToolCall("calc-1", "calculator", {"expression": "(18 + 24)"}),
        context,
    )
    unsafe = invoke(
        registry,
        ToolCall("calc-2", "calculator", {"expression": "__import__('os')"}),
        context,
    )

    assert success.ok and success.data == {"value": 42}
    assert not unsafe.ok
    assert unsafe.error is not None
    assert unsafe.error.code == "CALCULATOR_UNSAFE_EXPRESSION"


def test_unknown_and_invalid_arguments_never_execute_handler(tmp_path: Path) -> None:
    executions = 0

    async def handler(arguments, context):
        nonlocal executions
        executions += 1
        return ToolResult(ok=True, tool="counter", data={})

    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="counter",
            description="test",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            handler=handler,
        )
    )
    context = ToolContext("user_a", "one", SessionStore(tmp_path / "agent.db"))

    unknown = invoke(registry, ToolCall("1", "missing", {}), context)
    invalid = invoke(registry, ToolCall("2", "counter", {"extra": True}), context)

    assert unknown.error is not None and unknown.error.code == "UNKNOWN_TOOL"
    assert invalid.error is not None and invalid.error.code == "SCHEMA_VALIDATION_FAILED"
    assert executions == 0


def test_todo_uses_runtime_session_identity(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    registry = build_default_registry()
    one = ToolContext("user_a", "window_1", store)
    two = ToolContext("user_a", "window_2", store)

    added_one = invoke(
        registry,
        ToolCall("1", "todo", {"action": "add", "text": "带伞"}),
        one,
    )
    invoke(
        registry,
        ToolCall("2", "todo", {"action": "add", "text": "发送周报"}),
        two,
    )
    listed_one = invoke(registry, ToolCall("3", "todo", {"action": "list"}), one)
    listed_two = invoke(registry, ToolCall("4", "todo", {"action": "list"}), two)

    assert added_one.ok
    assert [item["text"] for item in listed_one.data["todos"]] == ["带伞"]
    assert [item["text"] for item in listed_two.data["todos"]] == ["发送周报"]


def test_search_and_weather_are_deterministic_local_tools(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    registry = build_default_registry()
    context = ToolContext("user_a", "one", store)

    search = invoke(
        registry,
        ToolCall("1", "search", {"query": "Context Compression", "top_k": 1}),
        context,
    )
    weather = invoke(
        registry, ToolCall("2", "weather", {"city": "上海"}), context
    )

    assert search.ok and search.data["mock"] is True
    assert search.data["results"][0]["source"].startswith("local://")
    assert weather.ok and weather.data["mock"] is True
    assert weather.data["advice"] == "建议带伞"


def test_registry_exports_schemas_and_rejects_bad_registration(tmp_path: Path) -> None:
    registry = build_default_registry()
    exported = registry.as_llm_tools()
    assert {item["function"]["name"] for item in exported} == {
        "calculator",
        "search",
        "todo",
        "weather",
    }
    assert all(item["function"]["parameters"]["type"] == "object" for item in exported)

    try:
        registry.register(registry.list_specs()[0])
    except ToolRegistrationError:
        pass
    else:
        raise AssertionError("duplicate registration must fail")

    async def handler(arguments, context):
        return ToolResult(ok=True, tool="bad", data={})

    try:
        ToolRegistry().register(
            ToolSpec(
                name="Bad-Name",
                description="bad",
                parameters={"type": "not-a-json-schema-type"},
                handler=handler,
            )
        )
    except ToolRegistrationError:
        pass
    else:
        raise AssertionError("invalid tool registration must fail")


def test_calculator_arithmetic_limits_and_data_tool_edge_cases(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    registry = build_default_registry()
    context = ToolContext("user", "session", store)

    expressions = {
        "1+2*3": 7,
        "-(2.5)+4": 1.5,
        "2**3": 8,
    }
    for index, (expression, expected) in enumerate(expressions.items()):
        result = invoke(
            registry,
            ToolCall(str(index), "calculator", {"expression": expression}),
            context,
        )
        assert result.ok and result.data["value"] == expected

    huge_power = invoke(
        registry,
        ToolCall("power", "calculator", {"expression": "2**999"}),
        context,
    )
    empty_search = invoke(
        registry,
        ToolCall("search", "search", {"query": "no-such-local-document", "top_k": 2}),
        context,
    )
    unknown_weather = invoke(
        registry,
        ToolCall("weather", "weather", {"city": "不存在城市"}),
        context,
    )
    assert not huge_power.ok
    assert empty_search.ok and empty_search.data["results"] == []
    assert not unknown_weather.ok
    assert unknown_weather.error is not None
    assert unknown_weather.error.code == "WEATHER_NO_DATA"


def test_todo_complete_delete_and_missing_id(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "agent.db")
    registry = build_default_registry()
    context = ToolContext("user", "session", store)
    added = invoke(
        registry,
        ToolCall("add", "todo", {"action": "add", "text": "交笔试"}),
        context,
    )
    todo_id = added.data["todo"]["id"]
    completed = invoke(
        registry,
        ToolCall("complete", "todo", {"action": "complete", "todo_id": todo_id}),
        context,
    )
    deleted = invoke(
        registry,
        ToolCall("delete", "todo", {"action": "delete", "todo_id": todo_id}),
        context,
    )
    missing = invoke(
        registry,
        ToolCall("missing", "todo", {"action": "complete", "todo_id": todo_id}),
        context,
    )

    assert completed.ok and completed.data["todo"]["status"] == "done"
    assert deleted.ok and deleted.data["deleted"] is True
    assert not missing.ok and missing.error is not None
    assert missing.error.code == "TODO_NOT_FOUND"
