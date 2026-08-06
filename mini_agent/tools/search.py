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


def _suggested_terms() -> list[str]:
    seen: list[str] = []
    for item in _load_corpus():
        for keyword in item.get("keywords", []):
            if keyword not in seen:
                seen.append(keyword)
    return seen


def _rerank(
    query: str,
    candidates: list[tuple[int, int, dict[str, str]]],
) -> list[tuple[int, int, dict[str, str]]]:
    """Lightweight second-stage ranking over recalled candidates.

    The score formula is intentionally replaceable: when the corpus grows,
    swap this function for an embedding/cross-encoder reranker without
    changing the caller.
    """

    terms = _terms(query)
    if not terms:
        return candidates

    def rank(item: dict[str, str]) -> tuple[float, int, int, int]:
        title = item["title"].casefold()
        snippet = item["snippet"].casefold()
        keywords = " ".join(item.get("keywords", [])).casefold()
        title_hits = sum(title.count(term) for term in terms)
        keyword_hits = sum(keywords.count(term) for term in terms)
        snippet_hits = sum(snippet.count(term) for term in terms)
        covered = sum(
            1 for term in terms if term in f"{title} {keywords} {snippet}"
        )
        coverage = covered / len(terms)
        return (coverage, title_hits * 3 + keyword_hits * 2, title_hits, snippet_hits)

    return sorted(candidates, key=lambda item: rank(item[2]), reverse=True)


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
    # 粗召回后轻量精排：保证语料扩大后仍有稳定的 top_k 排序。
    recall_k = max(10, top_k * 3)
    reranked = _rerank(query, ranked[:recall_k])
    results = [
        {
            "title": item["title"],
            "source": item["source"],
            "snippet": item["snippet"][:160],
        }
        for _, _, item in reranked[:top_k]
    ]
    data: dict[str, Any] = {"query": query, "results": results, "mock": True}
    if not results:
        data["suggested"] = _suggested_terms()
    return ToolResult(ok=True, tool="search", data=data)


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

