"""FastAPI entrypoint for local Kakao Live RAG.

Serves health, stats, ingest, and retrieval endpoints
against the local SQLite-backed message store.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request

try:
    from .embedding_client import ExternalEmbeddingClient
    from .semantic_index import DEFAULT_SEMANTIC_TOP_K, reciprocal_rank_fuse
    from .reranker import DEFAULT_RERANK_MODE, DEFAULT_RERANK_TOP_N, rerank_hits
    from .store import LiveRAGStore
except ImportError:
    from embedding_client import ExternalEmbeddingClient
    from semantic_index import DEFAULT_SEMANTIC_TOP_K, reciprocal_rank_fuse
    from reranker import DEFAULT_RERANK_MODE, DEFAULT_RERANK_TOP_N, rerank_hits
    from store import LiveRAGStore


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "live_rag.sqlite3"


def build_retrieval_response(store: LiveRAGStore, payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise ValueError("`query` is required.")
    requested_mode = str(payload.get("mode", "hybrid")).strip().lower()
    if requested_mode not in {"lexical", "semantic", "hybrid"}:
        raise ValueError("`mode` must be lexical, semantic, or hybrid.")

    limit = int(payload.get("limit", 8))
    context_before = int(payload.get("context_before", 2))
    context_after = int(payload.get("context_after", 2))
    semantic_top_k = int(payload.get("semantic_top_k", DEFAULT_SEMANTIC_TOP_K))
    rerank_mode = str(payload.get("rerank", DEFAULT_RERANK_MODE)).strip().lower()
    if rerank_mode not in {"off", "on", "auto"}:
        raise ValueError("`rerank` must be off, on, or auto.")
    rerank_top_n = int(payload.get("rerank_top_n", DEFAULT_RERANK_TOP_N))
    since_days_value = payload.get("since_days")
    since_days = float(since_days_value) if since_days_value is not None else None

    actual_mode = requested_mode
    fallback_reason: str | None = None
    lexical_hits: list[dict[str, Any]] = []
    semantic_hits: list[dict[str, Any]] = []
    settings = store.get_semantic_settings()
    query_profile_version: str | None = settings.get("query_profile_version") if settings else None

    if requested_mode in {"lexical", "hybrid"}:
        lexical_hits = store.retrieve_lexical(
            query=query,
            limit=limit,
            chat_id=payload.get("chat_id"),
            speaker=payload.get("speaker"),
            since_days=since_days,
            context_before=context_before,
            context_after=context_after,
        )

    if requested_mode in {"semantic", "hybrid"}:
        if settings is None:
            if requested_mode == "semantic":
                raise ValueError("Semantic index is not built yet.")
            actual_mode = "lexical"
            fallback_reason = "semantic_unavailable"
        else:
            try:
                client = ExternalEmbeddingClient(
                    model=str(settings["embedding_model"]),
                    provider=settings.get("embedding_provider"),
                )
                query_profile_version = client.query_profile_version
                semantic_query_vector = client.embed_query(query)
            except RuntimeError as error:
                if requested_mode == "semantic":
                    raise RuntimeError(str(error)) from error
                actual_mode = "lexical"
                fallback_reason = "semantic_unavailable"
            else:
                if requested_mode == "semantic":
                    semantic_hits = store.retrieve_semantic(
                        query_vector=semantic_query_vector,
                        limit=limit,
                        semantic_top_k=semantic_top_k,
                        chat_id=payload.get("chat_id"),
                        speaker=payload.get("speaker"),
                        since_days=since_days,
                        context_before=context_before,
                        context_after=context_after,
                        config_signature=str(settings["config_signature"]),
                    )
                    actual_mode = "semantic"
                else:
                    semantic_hits = store.retrieve_semantic(
                        query_vector=semantic_query_vector,
                        limit=limit,
                        semantic_top_k=semantic_top_k,
                        chat_id=payload.get("chat_id"),
                        speaker=payload.get("speaker"),
                        since_days=since_days,
                        context_before=context_before,
                        context_after=context_after,
                        config_signature=str(settings["config_signature"]),
                    )

    if actual_mode == "lexical":
        hits = lexical_hits
    elif actual_mode == "semantic":
        hits = semantic_hits
    else:
        hits = reciprocal_rank_fuse(lexical_hits, semantic_hits, limit=limit)

    hits, rerank_applied = rerank_hits(
        query=query,
        hits=hits,
        mode=actual_mode,
        rerank_mode=rerank_mode,
        limit=limit,
        top_n=rerank_top_n,
    )

    retrieval_sources = sorted({source for hit in hits for source in hit.get("retrieval_sources", [])})
    response = {
        "query": query,
        "requested_mode": requested_mode,
        "actual_mode": actual_mode,
        "mode": actual_mode,
        "fallback_reason": fallback_reason,
        "retrieval_sources": retrieval_sources,
        "semantic_config_signature": settings.get("config_signature") if settings else None,
        "embedding_model": settings.get("embedding_model") if settings else None,
        "embedding_provider": settings.get("embedding_provider") if settings else None,
        "query_profile_version": query_profile_version,
        "rerank_mode": rerank_mode,
        "rerank_applied": rerank_applied,
        "hits": hits,
    }
    if not fallback_reason:
        response.pop("fallback_reason")
    return response


def create_app() -> FastAPI:
    app = FastAPI(title="kakaocli live rag", version="0.1.0")
    db_path = Path(os.environ.get("LIVE_RAG_DB_PATH", DEFAULT_DB_PATH))
    app.state.store = LiveRAGStore(db_path)
    app.state.db_path = db_path

    @app.get("/health")
    def health() -> dict[str, Any]:
        stats = app.state.store.stats()
        return {
            "status": "ok",
            "db_path": str(app.state.db_path),
            "message_count": stats.get("message_count", 0),
            "chat_count": stats.get("chat_count", 0),
            "last_ingested_log_id": stats.get("last_ingested_log_id"),
        }

    @app.get("/stats")
    def stats() -> dict[str, Any]:
        return app.state.store.stats()

    @app.post("/kakao")
    async def ingest(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="Expected a JSON array of messages.")
        result = app.state.store.ingest_messages(payload, source="webhook")
        return result

    @app.get("/messages")
    def messages(
        limit: int = 50,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
    ) -> dict[str, Any]:
        return {
            "items": app.state.store.list_messages(
                limit=limit,
                chat_id=chat_id,
                speaker=speaker,
                since_days=since_days,
            )
        }

    @app.post("/retrieve")
    async def retrieve(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Expected a JSON object.")
        try:
            return build_retrieval_response(app.state.store, payload)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local live RAG webhook server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
