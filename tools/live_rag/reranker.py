"""Rerank fused hits with lightweight lexical evidence."""

from __future__ import annotations

import re
from typing import Any


TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
DEFAULT_RERANK_MODE = "auto"
DEFAULT_RERANK_TOP_N = 12


def rerank_hits(
    *,
    query: str,
    hits: list[dict[str, Any]],
    mode: str,
    rerank_mode: str = DEFAULT_RERANK_MODE,
    limit: int,
    top_n: int = DEFAULT_RERANK_TOP_N,
) -> tuple[list[dict[str, Any]], bool]:
    if rerank_mode == "off" or mode == "lexical" or len(hits) <= 1:
        return hits[:limit], False

    query_tokens = _tokens(query)
    if not query_tokens:
        return hits[:limit], False

    candidate_count = min(len(hits), top_n)
    prefix = [dict(hit) for hit in hits[:candidate_count]]
    suffix = hits[candidate_count:]
    if rerank_mode == "auto" and not _should_rerank(prefix):
        return hits[:limit], False

    for hit in prefix:
        base_score = float(hit.get("fusion_score") or hit.get("semantic_score") or hit.get("lexical_score") or hit.get("score") or 0.0)
        overlap = _overlap_score(query_tokens, hit)
        rerank_score = base_score + overlap
        hit["rerank_score"] = rerank_score
        hit["score"] = rerank_score

    ranked = sorted(
        prefix,
        key=lambda item: (
            -float(item.get("rerank_score", item.get("score", 0.0))),
            item["message"].get("timestamp", ""),
            int(item["message"]["log_id"]),
        ),
    )
    ranked.extend(suffix)
    return ranked[:limit], True


def _should_rerank(hits: list[dict[str, Any]]) -> bool:
    source_counts = {len(hit.get("retrieval_sources", [])) for hit in hits}
    return any(count > 1 for count in source_counts) or len({hit["message"]["log_id"] for hit in hits}) > 1


def _overlap_score(query_tokens: set[str], hit: dict[str, Any]) -> float:
    text_parts = [
        hit["message"].get("text") or "",
        hit.get("matched_chunk_text") or "",
        hit["message"].get("sender") or "",
        hit["message"].get("chat_name") or "",
    ]
    candidate_tokens = _tokens(" ".join(text_parts))
    if not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
    exact_sender = 0.02 if hit["message"].get("sender") and hit["message"]["sender"] in query_tokens else 0.0
    return overlap * 0.05 + exact_sender


def _tokens(text: str) -> set[str]:
    return {match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)}
