from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: Path) -> None:
    """Load a small, dependency-free subset of .env syntax.

    Existing process variables always win, so command-line environments can
    safely override values stored in the local file.
    """

    if not path.is_file():
        return

    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"{path} 第 {line_number} 行不是 KEY=VALUE 格式。")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not _ENV_KEY.fullmatch(key):
            raise ConfigError(f"{path} 第 {line_number} 行的环境变量名无效：{key!r}。")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数，当前值为 {raw!r}。") from exc
    if value < minimum:
        raise ConfigError(f"{name} 必须不小于 {minimum}，当前值为 {value}。")
    return value


def _optional_positive_int_env(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw or raw == "0":
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是正整数或 0，当前值为 {raw!r}。") from exc
    if value < 1:
        raise ConfigError(f"{name} 必须是正整数或 0，当前值为 {value}。")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    deepseek_api_key: str | None
    deepseek_base_url: str
    deepseek_model: str
    deepseek_thinking: str
    deepseek_reasoning_effort: str
    deepseek_max_tokens: int
    deepseek_timeout_seconds: int
    deepseek_max_retries: int
    max_llm_rounds_per_turn: int
    max_tool_calls_per_turn: int
    max_protocol_errors: int
    max_consecutive_tool_errors: int
    max_repeated_calls: int
    max_total_tokens_per_turn: int | None
    max_context_chars: int
    keep_recent_messages: int
    data_dir: Path
    database_path: Path

    @classmethod
    def from_env(cls, env_file: str | Path | None = None) -> "Settings":
        env_path = Path(env_file) if env_file is not None else Path.cwd() / ".env"
        load_env_file(env_path)

        data_dir = Path(os.environ.get("AGENT_DATA_DIR", ".agent_data")).expanduser()
        if not data_dir.is_absolute():
            data_dir = Path.cwd() / data_dir
        data_dir = data_dir.resolve()

        thinking = os.environ.get("DEEPSEEK_THINKING", "enabled")
        if thinking not in {"enabled", "disabled"}:
            raise ConfigError("DEEPSEEK_THINKING 只能是 enabled 或 disabled。")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
        if not model:
            raise ConfigError("DEEPSEEK_MODEL 不能为空。")

        return cls(
            deepseek_api_key=os.environ.get("DEEPSEEK_API_KEY") or None,
            deepseek_base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=model,
            deepseek_thinking=thinking,
            deepseek_reasoning_effort=os.environ.get("DEEPSEEK_REASONING_EFFORT", "max"),
            deepseek_max_tokens=_int_env("DEEPSEEK_MAX_TOKENS", 4096),
            deepseek_timeout_seconds=_int_env("DEEPSEEK_TIMEOUT_SECONDS", 60),
            deepseek_max_retries=_int_env("DEEPSEEK_MAX_RETRIES", 3, minimum=0),
            max_llm_rounds_per_turn=_int_env("MAX_LLM_ROUNDS_PER_TURN", 8),
            max_tool_calls_per_turn=_int_env("MAX_TOOL_CALLS_PER_TURN", 12),
            max_protocol_errors=_int_env("MAX_PROTOCOL_ERRORS", 2),
            max_consecutive_tool_errors=_int_env("MAX_CONSECUTIVE_TOOL_ERRORS", 3),
            max_repeated_calls=_int_env("MAX_REPEATED_CALLS", 2),
            max_total_tokens_per_turn=_optional_positive_int_env(
                "MAX_TOTAL_TOKENS_PER_TURN"
            ),
            max_context_chars=_int_env("MAX_CONTEXT_CHARS", 30_000),
            keep_recent_messages=_int_env("KEEP_RECENT_MESSAGES", 12),
            data_dir=data_dir,
            database_path=data_dir / "agent.db",
        )

    def require_deepseek_api_key(self) -> str:
        if not self.deepseek_api_key:
            raise ConfigError(
                "缺少 DEEPSEEK_API_KEY。请复制 .env.example 为 .env，"
                "填入 API key 后重试。"
            )
        return self.deepseek_api_key
