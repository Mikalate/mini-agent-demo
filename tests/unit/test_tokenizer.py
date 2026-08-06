from __future__ import annotations

import json

from mini_agent.llm.tokenizer import (
    _TOKENIZER_PATH,
    count_messages_tokens,
    count_tokens,
)


def test_bundled_tokenizer_file_exists() -> None:
    assert _TOKENIZER_PATH.is_file()
    assert _TOKENIZER_PATH.stat().st_size > 1_000_000


def test_count_tokens_basic() -> None:
    assert count_tokens("") == 0
    assert count_tokens("hello world") == 2
    assert count_tokens("你好世界") > 0


def test_count_messages_tokens_matches_serialized_json() -> None:
    messages = [{"role": "user", "content": "你好，请介绍一下项目"}]
    serialized = json.dumps(messages, ensure_ascii=False)
    assert count_messages_tokens(messages) == count_tokens(serialized)
    assert count_messages_tokens([]) == count_tokens("[]")


def test_tokenizer_is_reusable_and_deterministic() -> None:
    first = count_tokens("保持一致的计数")
    second = count_tokens("保持一致的计数")
    assert first == second > 0
