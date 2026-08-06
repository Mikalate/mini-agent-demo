from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from mini_agent.tools.base import ToolContext, ToolFailure, ToolResult, ToolSpec


def _load_fixture() -> dict[str, Any]:
    text = files("data").joinpath("weather_fixture.json").read_text(encoding="utf-8")
    return json.loads(text)


async def _weather(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    del context
    city = arguments["city"].strip()
    requested_date = arguments.get("date")
    fixture = _load_fixture()
    city_data = fixture.get(city.casefold())
    if city_data is None:
        raise ToolFailure(
            "WEATHER_NO_DATA",
            f"本地演示数据中没有 {city} 的天气；可用城市：{', '.join(fixture)}。",
            retryable=True,
        )
    key = requested_date or "default"
    weather = city_data.get(key)
    if weather is None:
        available_dates = sorted(
            {date for date in city_data if date != "default"}
        )
        raise ToolFailure(
            "WEATHER_NO_DATA",
            f"本地演示数据中没有 {city} 在 {requested_date} 的天气；"
            f"可用日期：{', '.join(available_dates) or '无'}（不传 date 时使用默认值）。",
            retryable=True,
        )
    return ToolResult(
        ok=True,
        tool="weather",
        data={"city": city, "date": requested_date or weather["date"], **weather, "mock": True},
    )


def weather_spec() -> ToolSpec:
    return ToolSpec(
        name="weather",
        description="读取内置的可重复天气演示数据；返回结果始终标记 mock=true。",
        parameters={
            "type": "object",
            "properties": {
                "city": {"type": "string", "minLength": 1, "maxLength": 50},
                "date": {"type": "string", "format": "date"},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
        handler=_weather,
    )

