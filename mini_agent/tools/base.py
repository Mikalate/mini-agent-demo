from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from mini_agent.sessions.store import SessionStore


@dataclass(frozen=True, slots=True)
class ToolContext:
    user_id: str
    session_id: str
    store: SessionStore
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ToolErrorInfo:
    code: str
    message: str
    retryable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


@dataclass(slots=True)
class ToolResult:
    ok: bool
    tool: str
    data: Any = None
    error: ToolErrorInfo | None = None
    meta: dict[str, Any] = field(
        default_factory=lambda: {
            "duration_ms": 0,
            "truncated": False,
            "source": "trusted_local_tool",
        }
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "data": self.data,
            "error": self.error.as_dict() if self.error else None,
            "meta": self.meta,
        }


class ToolFailure(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


ArgumentValidator = Callable[[dict[str, Any]], None]
ToolHandler = Callable[[dict[str, Any], ToolContext], Awaitable[ToolResult]]


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler
    argument_validator: ArgumentValidator | None = None

