from __future__ import annotations

import json
import re
from importlib.resources import files
from typing import Any

from mini_agent.tools.base import ToolContext, ToolResult, ToolSpec


def _load_corpus() -> list[dict[str, str]]:
    text = files("data").joinpath("search_corpus.json").read_text(encoding="utf-8")
    return json.loads(text)


def _terms(query: str) -> list[str]:
    lowered = query.casefold()
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]{2,}", lowered)
    return list(dict.fromkeys(words or [lowered]))


async def _search(arguments: dict[str, Any], context: ToolContext) -> ToolResult:
    del context
    query = arguments["query"].strip()
    top_k = arguments.get("top_k", 3)
    terms = _terms(query)
    ranked: list[tuple[int, int, dict[str, str]]] = []
    for index, item in enumerate(_load_corpus()):
        title = item["title"].casefold()
        snippet = item["snippet"].casefold()
        keywords = " ".join(item.get("keywords", [])).casefold()
        score = sum(4 * title.count(term) + 2 * keywords.count(term) + snippet.count(term) for term in terms)
        if score:
            ranked.append((score, -index, item))
    ranked.sort(reverse=True)
    results = [
        {
            "title": item["title"],
            "source": item["source"],
            "snippet": item["snippet"],
            "score": score,
        }
        for score, _, item in ranked[:top_k]
    ]
    return ToolResult(
        ok=True,
        tool="search",
        data={"query": query, "results": results, "mock": True},
    )


def search_spec() -> ToolSpec:
    return ToolSpec(
        name="search",
        description="在仓库内置的演示语料中执行确定性搜索；它不会访问互联网。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 200},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 5, "default": 3},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        handler=_search,
    )

