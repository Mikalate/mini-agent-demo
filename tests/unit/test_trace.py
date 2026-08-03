from __future__ import annotations

import json

from mini_agent.core.trace import (
    EventBus,
    JSONLTraceWriter,
    PlainRenderer,
    TraceEvent,
)


class BrokenSink:
    def emit(self, event: TraceEvent) -> None:
        raise RuntimeError("renderer failed")


def test_trace_redacts_secrets_and_sink_failure_is_isolated(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    output: list[str] = []
    bus = EventBus(
        [BrokenSink(), JSONLTraceWriter(path), PlainRenderer(output.append)]
    )

    bus.emit(
        TraceEvent(
            "tool_call_end",
            "run-1",
            data={
                "tool": "search",
                "ok": True,
                "args": {"api_key": "must-not-appear"},
                "authorization": "Bearer secret-token",
                "reasoning_content": "hidden-chain",
                "result_summary": "credential sk-abcdefghijklmnop",
                "duration_ms": 2,
            },
        )
    )

    raw = path.read_text(encoding="utf-8")
    event = json.loads(raw)
    assert event["args"]["api_key"] == "***"
    assert event["authorization"] == "***"
    assert "reasoning_content" not in event
    assert "hidden-chain" not in raw
    assert "must-not-appear" not in raw
    assert "secret-token" not in raw
    assert "abcdefghijklmnop" not in raw
    assert output and "hidden-chain" not in output[0]


def test_jsonl_writer_uses_one_event_per_line(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    writer = JSONLTraceWriter(path)
    writer.emit(TraceEvent("run_start", "run-1"))
    writer.emit(TraceEvent("run_end", "run-1", data={"status": "completed"}))

    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [event["event"] for event in events] == ["run_start", "run_end"]
