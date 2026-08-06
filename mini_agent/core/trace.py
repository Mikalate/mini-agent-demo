from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol


LOGGER = logging.getLogger(__name__)
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "reasoning_content",
}
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s,;]+")
_KEY_LIKE = re.compile(r"\b(?:sk|ds)-[A-Za-z0-9_-]{12,}\b")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def redact(value: Any) -> Any:
    """Return a JSON-safe copy with credentials and hidden reasoning removed."""

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            lowered = key.lower()
            if lowered == "reasoning_content":
                continue
            if lowered in _SENSITIVE_KEYS or lowered.endswith("_api_key"):
                cleaned[key] = "***"
            else:
                cleaned[key] = redact(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _KEY_LIKE.sub("***", _BEARER.sub("Bearer ***", value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def compact_text(value: Any, limit: int = 240) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(redact(value), ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else f"{text[: limit - 1]}…"


@dataclass(frozen=True, slots=True)
class TraceEvent:
    event: str
    run_id: str
    timestamp: str = field(default_factory=utc_now)
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "run_id": self.run_id,
            "timestamp": self.timestamp,
            **redact(self.data),
        }


class TraceSink(Protocol):
    def emit(self, event: TraceEvent) -> None: ...


class JSONLTraceWriter:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def emit(self, event: TraceEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event.as_dict(), ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")


class EventBus:
    """Best-effort fan-out; an observation failure never changes runtime flow."""

    def __init__(self, sinks: Iterable[TraceSink] = ()):
        self.sinks = list(sinks)

    def emit(self, event: TraceEvent) -> None:
        for sink in self.sinks:
            try:
                sink.emit(event)
            except Exception:
                LOGGER.exception("Trace sink failed for event %s", event.event)


class RunTrace:
    def __init__(self, run_id: str, bus: EventBus):
        self.run_id = run_id
        self.bus = bus

    def emit(self, event: str, **data: Any) -> None:
        self.bus.emit(TraceEvent(event=event, run_id=self.run_id, data=data))


def _event_line(event: TraceEvent) -> tuple[str | None, str | None]:
    data = redact(event.data)
    kind = event.event
    if kind == "run_start":
        return (
            f"Mini Agent · user={data.get('user_id')} · "
            f"session={data.get('session_id')} · model={data.get('model')}",
            "cyan",
        )
    if kind == "llm_call_start":
        return f"[Round {data.get('round')}] 正在请求模型…", "dim"
    if kind == "assistant_decision":
        tools = data.get("tools") or []
        if tools:
            summary = data.get("public_summary") or "调用工具"
            return f"[Round {data.get('round')}] {summary}：{', '.join(tools)}", "cyan"
        return None, None
    if kind == "tool_call_start":
        args = compact_text(data.get("args", {}), 180)
        return f"  参数  {data.get('tool')} {args}", "dim"
    if kind == "tool_call_end":
        mark = "ok" if data.get("ok") else "err"
        duration = data.get("duration_ms", 0)
        summary = compact_text(data.get("result_summary", ""), 180)
        style = "green" if data.get("ok") else "red"
        return f"  结果  {mark} {summary}  {duration} ms", style
    if kind in {"retry", "context_compacted", "budget_warning"}:
        return compact_text(data.get("message") or data, 220), "yellow"
    if kind == "error":
        return f"错误 [{data.get('code')}] {compact_text(data.get('message', ''))}", "red"
    if kind == "run_end":
        cost = data.get("cost_usd", 0.0) or 0.0
        return (
            f"run: {event.run_id[:12]}  status: {data.get('status')}  "
            f"rounds: {data.get('rounds', 0)}  tools: {data.get('tools', 0)}  "
            f"tokens: {data.get('tokens', 0)}  cost: ${cost:.6f}  "
            f"{data.get('duration_ms', 0)} ms",
            "bold" if data.get("status") == "completed" else "yellow",
        )
    return None, None


class PlainRenderer:
    def __init__(self, output: Callable[[str], None] = print):
        self.output = output

    def emit(self, event: TraceEvent) -> None:
        line, _ = _event_line(event)
        if line:
            self.output(line)


class RichRenderer:
    def __init__(self, console: Any | None = None):
        if console is None:
            from rich.console import Console

            console = Console()
        self.console = console

    def emit(self, event: TraceEvent) -> None:
        line, style = _event_line(event)
        if line:
            self.console.print(line, style=style)


def create_run_trace(
    data_dir: str | Path,
    run_id: str,
    renderer: TraceSink | None = None,
    *,
    extra_sinks: Iterable[TraceSink] = (),
) -> RunTrace:
    sinks: list[TraceSink] = [
        JSONLTraceWriter(Path(data_dir) / "runs" / run_id / "trace.jsonl")
    ]
    if renderer is not None:
        sinks.append(renderer)
    sinks.extend(extra_sinks)
    return RunTrace(run_id, EventBus(sinks))
