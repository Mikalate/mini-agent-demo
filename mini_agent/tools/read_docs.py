from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import Any

from mini_agent.tools.base import ToolContext, ToolFailure, ToolResult, ToolSpec


def _load_index() -> list[dict[str, str]]:
    text = files("data").joinpath("docs_index.json").read_text(encoding="utf-8")
    return json.loads(text)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _read_text_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="gbk")


async def _read_docs(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    del context
    doc_id = arguments["doc_id"].strip()
    max_chars = arguments.get("max_chars", 3000)
    index = _load_index()
    entry = next((item for item in index if item["id"] == doc_id), None)
    if entry is None:
        available = ", ".join(item["id"] for item in index)
        raise ToolFailure(
            "DOC_NOT_FOUND",
            f"read_docs 仅支持以下文档：{available}。",
            retryable=True,
        )

    target = (_project_root() / entry["path"]).resolve()
    if not target.is_relative_to(_project_root()):
        raise ToolFailure("DOC_READ_FAILED", "文档路径超出项目根目录。", retryable=False)
    suffix = target.suffix.casefold()
    if suffix not in {".md", ".txt"}:
        raise ToolFailure(
            "DOC_FORMAT_UNSUPPORTED",
            f"read_docs 仅支持 md/txt，文档 {entry['id']} 是 {suffix or '未知'} 格式。",
            retryable=False,
        )
    try:
        content = _read_text_file(target)
    except OSError:
        raise ToolFailure("DOC_READ_FAILED", f"无法读取文档 {entry['id']}。", retryable=False)

    return ToolResult(
        ok=True,
        tool="read_docs",
        data={
            "doc_id": entry["id"],
            "title": entry["title"],
            "content": content[:max_chars],
            "chars": len(content),
            "truncated": len(content) > max_chars,
            "format": suffix.lstrip("."),
            "mock": True,
        },
    )


def read_docs_spec() -> ToolSpec:
    return ToolSpec(
        name="read_docs",
        description=(
            "读取仓库内置白名单文档（README、运行时 Prompt、关键工程问题、演示笔记；"
            "支持 md/txt）；本地文件，不访问互联网；未知 doc_id 会返回可用列表。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "minLength": 1, "maxLength": 64},
                "max_chars": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 8000,
                    "default": 3000,
                },
            },
            "required": ["doc_id"],
            "additionalProperties": False,
        },
        handler=_read_docs,
    )
