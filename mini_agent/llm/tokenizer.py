from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

_TOKENIZER_PATH = Path(__file__).parent / "tokenizer" / "deepseek_tokenizer.json"
_lock = threading.Lock()
_tokenizer: Any = None


def get_tokenizer() -> Any:
    """Load the bundled DeepSeek tokenizer once, thread-safely and offline."""
    global _tokenizer
    if _tokenizer is None:
        with _lock:
            if _tokenizer is None:
                if not _TOKENIZER_PATH.is_file():
                    raise FileNotFoundError(
                        f"缺少打包的 DeepSeek tokenizer：{_TOKENIZER_PATH}"
                    )
                from tokenizers import Tokenizer

                _tokenizer = Tokenizer.from_file(str(_TOKENIZER_PATH))
    return _tokenizer


def count_tokens(text: str) -> int:
    if not text:
        return 0
    return len(get_tokenizer().encode(text).ids)


def count_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate tokens for the exact JSON that will be sent to the API."""
    return count_tokens(json.dumps(messages, ensure_ascii=False, default=str))
