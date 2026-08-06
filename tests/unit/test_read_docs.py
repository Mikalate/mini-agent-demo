import asyncio
from pathlib import Path

from mini_agent.core.models import ToolCall
from mini_agent.sessions.store import SessionStore
from mini_agent.tools.base import ToolContext
from mini_agent.tools.registry import build_default_registry


def invoke(registry, call: ToolCall, context: ToolContext):
    return asyncio.run(registry.invoke(call, context))


def context(tmp_path: Path) -> ToolContext:
    return ToolContext("user", "session", SessionStore(tmp_path / "agent.db"))


def test_read_docs_returns_registered_markdown(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(), ToolCall("1", "read_docs", {"doc_id": "readme"}), context(tmp_path)
    )
    assert result.ok
    assert result.data["doc_id"] == "readme"
    assert result.data["format"] == "md"
    assert result.data["mock"] is True
    assert "Mini Agent" in result.data["content"]


def test_read_docs_supports_txt(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(), ToolCall("2", "read_docs", {"doc_id": "demo-notes"}), context(tmp_path)
    )
    assert result.ok
    assert result.data["doc_id"] == "demo-notes"
    assert result.data["format"] == "txt"
    assert "五个本地工具" in result.data["content"]


def test_read_docs_unknown_doc_lists_available_ids(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(), ToolCall("3", "read_docs", {"doc_id": "missing"}), context(tmp_path)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "DOC_NOT_FOUND"
    assert result.error.retryable
    assert "readme" in result.error.message and "demo-notes" in result.error.message


def test_read_docs_truncates_long_content(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(),
        ToolCall("4", "read_docs", {"doc_id": "readme", "max_chars": 200}),
        context(tmp_path),
    )
    assert result.ok
    assert result.data["truncated"] is True
    assert len(result.data["content"]) <= 200
    assert result.data["chars"] > 200


def test_read_docs_rejects_path_injection(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(),
        ToolCall("5", "read_docs", {"doc_id": "../APIkey.txt"}),
        context(tmp_path),
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "DOC_NOT_FOUND"


def test_read_docs_missing_doc_id_fails_schema(tmp_path: Path) -> None:
    result = invoke(
        build_default_registry(), ToolCall("6", "read_docs", {}), context(tmp_path)
    )
    assert not result.ok
    assert result.error is not None and result.error.code == "SCHEMA_VALIDATION_FAILED"
