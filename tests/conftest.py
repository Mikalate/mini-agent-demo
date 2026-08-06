from __future__ import annotations

from pathlib import Path

import pytest

from mini_agent.config import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    values = {
        "DEEPSEEK_API_KEY": "test-key-not-real",
        "DEEPSEEK_BASE_URL": "https://api.deepseek.com",
        "DEEPSEEK_MODEL": "deepseek-v4-flash",
        "DEEPSEEK_THINKING": "enabled",
        "DEEPSEEK_REASONING_EFFORT": "max",
        "DEEPSEEK_MAX_TOKENS": "4096",
        "DEEPSEEK_TIMEOUT_SECONDS": "60",
        "DEEPSEEK_MAX_RETRIES": "3",
        "MAX_LLM_ROUNDS_PER_TURN": "8",
        "MAX_TOOL_CALLS_PER_TURN": "12",
        "MAX_PROTOCOL_ERRORS": "2",
        "MAX_CONSECUTIVE_TOOL_ERRORS": "3",
        "MAX_REPEATED_CALLS": "2",
        "MAX_CONTEXT_TOKENS": "12000",
        "KEEP_RECENT_MESSAGES": "12",
        "AGENT_DATA_DIR": str(tmp_path / "agent-data"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings.from_env(tmp_path / "missing.env")

