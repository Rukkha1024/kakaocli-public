"""Deterministic fixture corpus and retrieval evaluation helpers."""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .build_semantic_index import build_semantic_index
    from .policy import SemanticPolicy
    from .store import LiveRAGStore
except ImportError:
    from build_semantic_index import build_semantic_index
    from policy import SemanticPolicy
    from store import LiveRAGStore


EVAL_ROOT = Path(__file__).resolve().parent / "eval"
BENCHMARK_CASES_PATH = EVAL_ROOT / "benchmark_cases.json"
REFERENCE_SNAPSHOT_PATH = EVAL_ROOT / "reference_snapshot.json"
FIXTURE_EMBEDDING_MODEL = "fixture/deterministic-v1"
FIXTURE_EMBEDDING_PROVIDER = "fixture"
QUERY_PROFILE_VERSION = "fixture-query-v1"
TOKEN_PATTERN = re.compile(r"[0-9A-Za-z가-힣]+")
CONCEPTS = (
    "meeting",
    "delay",
    "draft",
    "lunch",
    "parkdahoon",
    "date_mar5",
    "minji",
    "design_review",
    "large_group",
    "live_recent",
    "server_notice",
    "update",
)
KEYWORDS = {
    "meeting": ("회의", "미팅", "일정", "리뷰"),
    "delay": ("연기", "미뤄", "옮", "변경", "바뀌"),
    "draft": ("자료", "초안", "올려", "업로드", "제출"),
    "lunch": ("점심", "먹", "식사"),
    "parkdahoon": ("박다훈", "다훈"),
    "date_mar5": ("3월", "5일", "목요일", "오후", "2시"),
    "minji": ("민지",),
    "design_review": ("디자인", "리뷰"),
    "large_group": ("대규모", "전체", "공지방"),
    "live_recent": ("오늘", "방금", "최신", "새로"),
    "server_notice": ("서버", "점검", "배포", "공지"),
    "update": ("업데이트", "변경", "수정"),
}
QUERY_EXPANSIONS = {
    "연기": ("미뤄", "옮"),
    "미뤄": ("연기", "옮"),
    "업데이트": ("변경", "수정"),
    "변경": ("업데이트", "수정"),
    "리뷰": ("회의",),
}
FIXTURE_MESSAGES = [
    {
        "type": "message",
        "log_id": 101,
        "chat_id": 9001,
        "chat_name": "프로젝트 공지",
        "sender_id": 11,
        "sender": "민지",
        "text": "내일 회의는 다음 주 화요일로 미뤄졌어요.",
        "message_type": 1,
        "timestamp": "2026-03-01T09:00:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 102,
        "chat_id": 9001,
        "chat_name": "프로젝트 공지",
        "sender_id": 12,
        "sender": "현우",
        "text": "자료 초안은 오늘 오후까지 올려 주세요.",
        "message_type": 1,
        "timestamp": "2026-03-01T09:03:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 103,
        "chat_id": 9001,
        "chat_name": "프로젝트 공지",
        "sender_id": 13,
        "sender": "지연",
        "text": "점심은 12시 30분에 같이 먹어요.",
        "message_type": 1,
        "timestamp": "2026-03-01T09:05:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 104,
        "chat_id": 9002,
        "chat_name": "제품 일정",
        "sender_id": 14,
        "sender": "박다훈",
        "text": "업데이트 일정은 오늘 저녁에 다시 공유할게요.",
        "message_type": 1,
        "timestamp": "2026-03-02T07:30:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 105,
        "chat_id": 9002,
        "chat_name": "제품 일정",
        "sender_id": 14,
        "sender": "박다훈",
        "text": "3월 5일 일정은 목요일 오후 2시입니다.",
        "message_type": 1,
        "timestamp": "2026-03-02T07:33:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 106,
        "chat_id": 9003,
        "chat_name": "인프라 공지",
        "sender_id": 15,
        "sender": "서연",
        "text": "서버 점검 공지는 금요일 밤 11시에 배포합니다.\n배포 전에는 로그인 지연이 발생할 수 있습니다.\n문의는 운영 채널에 남겨 주세요.",
        "message_type": 1,
        "timestamp": "2026-03-03T11:00:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 201,
        "chat_id": 9900,
        "chat_name": "전체 디자인 공지방",
        "sender_id": 16,
        "sender": "유진",
        "text": "디자인 리뷰는 금요일 오후로 옮깁니다.",
        "message_type": 1,
        "timestamp": "2026-03-04T08:00:00Z",
        "is_from_me": False,
    },
    {
        "type": "message",
        "log_id": 301,
        "chat_id": 9004,
        "chat_name": "운영 속보",
        "sender_id": 17,
        "sender": "지원",
        "text": "오늘 새로 올라온 배포 일정은 밤 10시입니다.",
        "message_type": 1,
        "timestamp": "2026-03-06T22:00:00Z",
        "is_from_me": False,
    },
]
FIXTURE_CHAT_METADATA = [
    {"id": 9001, "display_name": "프로젝트 공지", "member_count": 3, "type": "group"},
    {"id": 9002, "display_name": "제품 일정", "member_count": 4, "type": "group"},
    {"id": 9003, "display_name": "인프라 공지", "member_count": 12, "type": "group"},
    {"id": 9004, "display_name": "운영 속보", "member_count": 6, "type": "group"},
    {"id": 9900, "display_name": "전체 디자인 공지방", "member_count": 57, "type": "group"},
]


class DeterministicEmbeddingClient:
    """Deterministic embedding client for local tests."""

    model = FIXTURE_EMBEDDING_MODEL
    provider = FIXTURE_EMBEDDING_PROVIDER
    query_profile_version = QUERY_PROFILE_VERSION

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_text(text, query=False) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_text(text, query=True)

    def _embed_text(self, text: str, *, query: bool) -> list[float]:
        tokens = _extract_tokens(text)
        if query:
            tokens = _expand_query_tokens(tokens)
        weights = np.zeros(len(CONCEPTS), dtype=np.float32)
        normalized_text = " ".join(tokens)
        for index, concept in enumerate(CONCEPTS):
            score = 0.0
            for keyword in KEYWORDS[concept]:
                if keyword.lower() in normalized_text:
                    score += 1.0
            if score:
                weights[index] = score
        if query and "박다훈" in text:
            weights[CONCEPTS.index("update")] += 1.0
        if not np.any(weights):
            weights[0] = 1.0
        weights = weights / np.linalg.norm(weights)
        return weights.astype(float).tolist()


def load_benchmark_cases(path: Path = BENCHMARK_CASES_PATH) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"Benchmark cases missing from {path}")
    return cases


def seed_fixture_store(
    *,
    db_path: Path,
    policy: SemanticPolicy,
    embedding_client: Any | None = None,
) -> LiveRAGStore:
    client = embedding_client or DeterministicEmbeddingClient()
    store = LiveRAGStore(db_path)
    store.ingest_messages(FIXTURE_MESSAGES, source="fixture")
    build_semantic_index(
        store,
        client,
        mode="rebuild",
        limit=None,
        embedding_model=getattr(client, "model", FIXTURE_EMBEDDING_MODEL),
        embedding_provider=getattr(client, "provider", FIXTURE_EMBEDDING_PROVIDER),
        binary=None,
        chat_metadata=FIXTURE_CHAT_METADATA,
        policy=policy,
        embedding_request_batch_size=8,
        message_fetch_batch_size=8,
    )
    return store


def evaluate_benchmark(
    *,
    store: LiveRAGStore,
    client: Any,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    metrics_by_mode: dict[str, list[dict[str, Any]]] = {"lexical": [], "semantic": [], "hybrid": []}
    case_results: list[dict[str, Any]] = []
    settings = store.get_semantic_settings()
    for case in cases:
        per_mode: dict[str, Any] = {}
        for mode in ("lexical", "semantic", "hybrid"):
            hits = _retrieve_case_hits(
                store=store,
                client=client,
                case=case,
                mode=mode,
                config_signature=settings["config_signature"] if settings else None,
            )
            ranked_log_ids = [int(hit["message"]["log_id"]) for hit in hits]
            expected = [int(log_id) for log_id in case["expected_log_ids"]]
            metric_row = _metric_row(ranked_log_ids=ranked_log_ids, expected_log_ids=expected, k=int(case.get("limit", 3)))
            metrics_by_mode[mode].append(metric_row)
            per_mode[mode] = {
                "ranked_log_ids": ranked_log_ids,
                "matched": expected[0] in ranked_log_ids,
                "metrics": metric_row,
            }
        case_results.append({"case_id": case["id"], "query": case["query"], "results": per_mode})

    return {
        "cases": case_results,
        "metrics": {mode: _aggregate_metrics(rows) for mode, rows in metrics_by_mode.items()},
    }


def build_reference_snapshot(
    *,
    store: LiveRAGStore,
    client: Any,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    settings = store.get_semantic_settings()
    snapshot_cases = []
    for case in cases:
        payload = {"case_id": case["id"], "query": case["query"], "modes": {}}
        for mode in ("lexical", "semantic", "hybrid"):
            hits = _retrieve_case_hits(
                store=store,
                client=client,
                case=case,
                mode=mode,
                config_signature=settings["config_signature"] if settings else None,
            )
            payload["modes"][mode] = [
                {
                    "log_id": int(hit["message"]["log_id"]),
                    "sender": hit["message"].get("sender"),
                    "text": hit["message"].get("text"),
                    "retrieval_sources": hit.get("retrieval_sources", []),
                    "matched_chunk_id": hit.get("matched_chunk_id"),
                }
                for hit in hits[: int(case.get("limit", 3))]
            ]
        snapshot_cases.append(payload)
    return {"snapshot_version": 1, "cases": snapshot_cases}


def md5_hex(payload: dict[str, Any]) -> str:
    return hashlib.md5(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _retrieve_case_hits(
    *,
    store: LiveRAGStore,
    client: Any,
    case: dict[str, Any],
    mode: str,
    config_signature: str | None,
) -> list[dict[str, Any]]:
    kwargs = {
        "query": case["query"],
        "limit": int(case.get("limit", 3)),
        "chat_id": case.get("chat_id"),
        "speaker": case.get("speaker"),
        "since_days": case.get("since_days"),
        "context_before": 1,
        "context_after": 1,
    }
    if mode == "lexical":
        return store.retrieve_lexical(**kwargs)
    if mode == "semantic":
        query_vector = client.embed_query(case["query"])
        return store.retrieve_semantic(
            query_vector=query_vector,
            semantic_top_k=int(case.get("semantic_top_k", max(6, kwargs["limit"]))),
            config_signature=config_signature,
            **{key: value for key, value in kwargs.items() if key != "query"},
        )
    return store.retrieve_hybrid(
        semantic_query_vector=client.embed_query(case["query"]),
        semantic_top_k=int(case.get("semantic_top_k", max(6, kwargs["limit"]))),
        config_signature=config_signature,
        **kwargs,
    )


def _metric_row(*, ranked_log_ids: list[int], expected_log_ids: list[int], k: int) -> dict[str, float]:
    relevant = set(expected_log_ids)
    top_k = ranked_log_ids[:k]
    reciprocal_rank = 0.0
    for index, log_id in enumerate(ranked_log_ids, start=1):
        if log_id in relevant:
            reciprocal_rank = 1.0 / index
            break
    dcg = 0.0
    for index, log_id in enumerate(top_k, start=1):
        if log_id in relevant:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    precision = sum(1 for log_id in top_k if log_id in relevant) / max(1, k)
    recall = sum(1 for log_id in top_k if log_id in relevant) / max(1, len(relevant))
    return {
        "mrr": reciprocal_rank,
        "ndcg": 0.0 if ideal_dcg == 0.0 else dcg / ideal_dcg,
        "precision_at_k": precision,
        "recall_at_k": recall,
    }


def _aggregate_metrics(rows: list[dict[str, float]]) -> dict[str, float]:
    if not rows:
        return {"mrr": 0.0, "ndcg": 0.0, "precision_at_k": 0.0, "recall_at_k": 0.0}
    return {
        key: sum(float(row[key]) for row in rows) / len(rows)
        for key in ("mrr", "ndcg", "precision_at_k", "recall_at_k")
    }


def _extract_tokens(text: str) -> list[str]:
    return [match.group(0).lower() for match in TOKEN_PATTERN.finditer(text)]


def _expand_query_tokens(tokens: list[str]) -> list[str]:
    expanded = list(tokens)
    for token in list(tokens):
        expanded.extend(keyword.lower() for keyword in QUERY_EXPANSIONS.get(token, ()))
    return expanded
