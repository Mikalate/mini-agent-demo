from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None
    raw_arguments: str | None = None


@dataclass(slots=True)
class Message:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    run_id: str | None = None
    is_compressed: bool = False
    id: int | None = None
    created_at: datetime | None = None


@dataclass(slots=True)
class SessionRecord:
    id: int
    user_id: str
    session_id: str
    summary: str
    summary_version: int
    created_at: datetime
    last_active_at: datetime


@dataclass(slots=True)
class TodoRecord:
    id: str
    text: str
    status: Literal["pending", "done"]
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class RunState:
    run_id: str
    user_id: str
    session_id: str
    round: int = 0
    tool_step: int = 0
    api_attempts: int = 0
    successful_llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    consecutive_errors: int = 0
    repeated_call_count: int = 0
    seen_tool_call_ids: set[str] = field(default_factory=set)
    status: str = "running"
