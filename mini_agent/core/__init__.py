"""Core agent control-flow modules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mini_agent.core.agent import Agent, RunResult

__all__ = ["Agent", "RunResult"]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from mini_agent.core.agent import Agent, RunResult

        return {"Agent": Agent, "RunResult": RunResult}[name]
    raise AttributeError(name)
