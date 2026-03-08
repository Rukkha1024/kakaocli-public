"""Build or update the local semantic sidecar."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from .embedding_client import ExternalEmbeddingClient
    from .policy import DEFAULT_POLICY_PATH, SemanticPolicy, load_semantic_policy
    from .semantic_index import (
        DEFAULT_CHUNK_CHARS,
        DEFAULT_CHUNK_OVERLAP,
        DEFAULT_EMBEDDING_REQUEST_BATCH_SIZE,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MESSAGE_FETCH_BATCH_SIZE,
        DEFAULT_MAX_MEMBER_COUNT,
        batched,
        build_config_signature,
        chunk_message,
        normalize_vector,
    )
    from .store import LiveRAGStore
except ImportError:
    from embedding_client import ExternalEmbeddingClient
    from policy import DEFAULT_POLICY_PATH, SemanticPolicy, load_semantic_policy
    from semantic_index import (
        DEFAULT_CHUNK_CHARS,
        DEFAULT_CHUNK_OVERLAP,
        DEFAULT_EMBEDDING_REQUEST_BATCH_SIZE,
        DEFAULT_EMBEDDING_MODEL,
        DEFAULT_MESSAGE_FETCH_BATCH_SIZE,
        DEFAULT_MAX_MEMBER_COUNT,
        batched,
        build_config_signature,
        chunk_message,
        normalize_vector,
    )
    from store import LiveRAGStore


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "live_rag.sqlite3"
DEFAULT_BINARY = Path(__file__).resolve().parents[2] / ".build" / "release" / "kakaocli"
DEFAULT_CHAT_LIMIT = 5000


def load_chat_metadata(binary: str, *, chat_limit: int = DEFAULT_CHAT_LIMIT) -> list[dict[str, Any]]:
    result = subprocess.run(
        [binary, "chats", "--limit", str(chat_limit), "--json"],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or not payload:
        raise RuntimeError("`kakaocli chats --json` returned no chat metadata.")
    return payload


def build_semantic_index(
    store: LiveRAGStore,
    client: ExternalEmbeddingClient,
    *,
    mode: str,
    limit: int | None,
    embedding_model: str,
    embedding_provider: str | None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    max_member_count: int = DEFAULT_MAX_MEMBER_COUNT,
    message_fetch_batch_size: int = DEFAULT_MESSAGE_FETCH_BATCH_SIZE,
    embedding_request_batch_size: int = DEFAULT_EMBEDDING_REQUEST_BATCH_SIZE,
    progress: bool = False,
    binary: str | None = str(DEFAULT_BINARY),
    chat_metadata: list[dict[str, Any]] | None = None,
    policy: SemanticPolicy | None = None,
) -> dict[str, Any]:
    effective_policy = policy or load_semantic_policy()
    if effective_policy.default_max_member_count != max_member_count:
        effective_policy = SemanticPolicy(
            default_max_member_count=max_member_count,
            allow_chat_ids=effective_policy.allow_chat_ids,
            deny_chat_ids=effective_policy.deny_chat_ids,
            chat_overrides=effective_policy.chat_overrides,
            signature=effective_policy.signature,
            source_path=effective_policy.source_path,
            version=effective_policy.version,
        )
    config_signature = build_config_signature(
        embedding_model=embedding_model,
        embedding_provider=embedding_provider,
        chunk_chars=chunk_chars,
        chunk_overlap=chunk_overlap,
        max_member_count=effective_policy.default_max_member_count,
        policy_signature=effective_policy.signature,
    )
    existing = store.get_semantic_settings()
    if mode == "update" and existing and existing["config_signature"] != config_signature:
        raise RuntimeError(
            "Semantic config changed. Run with `--mode rebuild` to replace the old semantic sidecar."
        )

    try:
        refreshed_chat_metadata = chat_metadata
        if refreshed_chat_metadata is None:
            if not binary:
                raise RuntimeError("Chat metadata refresh requires a kakaocli binary path.")
            refreshed_chat_metadata = load_chat_metadata(binary)
        metadata_result = store.upsert_chat_metadata(refreshed_chat_metadata)
    except subprocess.CalledProcessError as error:
        stderr = (error.stderr or "").strip()
        raise RuntimeError(f"Failed to refresh chat metadata via `kakaocli chats --json`: {stderr or error}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("`kakaocli chats --json` returned invalid JSON.") from error

    missing_metadata_count = store.count_embedding_messages_missing_chat_metadata(
        after_log_id=None if mode == "rebuild" else (existing["last_indexed_log_id"] if existing else None),
        limit=limit,
    )
    if missing_metadata_count > 0:
        raise RuntimeError(
            "Chat metadata refresh was incomplete for semantic embedding candidates. "
            "Aborting before mutating semantic state."
        )

    if mode == "rebuild":
        store.clear_semantic_index()

    cursor = None if mode == "rebuild" else (existing["last_indexed_log_id"] if existing else None)
    remaining = limit
    processed_messages = 0
    indexed_messages = 0
    indexed_chunks = 0
    batches = 0
    last_log_id = cursor or 0

    while True:
        fetch_limit = message_fetch_batch_size if remaining is None else min(message_fetch_batch_size, remaining)
        if fetch_limit <= 0:
            break

        messages = store.iter_messages_for_embedding(
            after_log_id=cursor,
            limit=fetch_limit,
            policy=effective_policy,
        )
        if not messages:
            break

        chunks: list[dict[str, Any]] = []
        for message in messages:
            chunks.extend(
                chunk_message(
                    message,
                    config_signature=config_signature,
                    embedding_model=embedding_model,
                    embedding_provider=embedding_provider,
                    chunk_chars=chunk_chars,
                    chunk_overlap=chunk_overlap,
                )
            )

        vectors: list[list[float]] = []
        for embed_batch in batched([chunk["chunk_text"] for chunk in chunks], embedding_request_batch_size):
            vectors.extend(client.embed_documents(embed_batch))
        if len(vectors) != len(chunks):
            raise RuntimeError("Embedding response count did not match the number of chunks.")

        for chunk, vector in zip(chunks, vectors, strict=True):
            chunk["vector"] = normalize_vector(vector)

        upsert_result = store.upsert_semantic_chunks(chunks)
        cursor = int(messages[-1]["log_id"])
        last_log_id = cursor
        processed_messages += len(messages)
        indexed_messages += upsert_result["messages_indexed"]
        indexed_chunks += upsert_result["chunks_indexed"]
        batches += 1
        if remaining is not None:
            remaining -= len(messages)

        store.set_runtime_state("semantic_config_signature", config_signature)
        store.set_runtime_state("semantic_embedding_model", embedding_model)
        store.set_runtime_state("semantic_embedding_provider", embedding_provider or "")
        store.set_runtime_state("semantic_chunk_chars", str(chunk_chars))
        store.set_runtime_state("semantic_chunk_overlap", str(chunk_overlap))
        store.set_runtime_state("semantic_max_member_count", str(effective_policy.default_max_member_count))
        store.set_runtime_state("semantic_last_indexed_log_id", str(last_log_id))
        store.set_runtime_state("semantic_policy_signature", effective_policy.signature)
        store.set_runtime_state("semantic_policy_path", str(effective_policy.source_path))
        store.set_runtime_state("semantic_message_fetch_batch_size", str(message_fetch_batch_size))
        store.set_runtime_state("semantic_embedding_request_batch_size", str(embedding_request_batch_size))
        if hasattr(client, "query_profile_version"):
            store.set_runtime_state("semantic_query_profile_version", str(client.query_profile_version))

        if progress:
            print(
                json.dumps(
                    {
                        "stage": "build_index_progress",
                        "batch": batches,
                        "processed_messages": processed_messages,
                        "indexed_messages": indexed_messages,
                        "embedded_chunks": indexed_chunks,
                        "last_log_id": last_log_id,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )

        if remaining is not None and remaining <= 0:
            break

    return {
        "status": "ok",
        "mode": mode,
        "messages_considered": processed_messages,
        "embedded_chunks": indexed_chunks,
        "indexed_messages": indexed_messages,
        "updated_from_log_id": last_log_id,
        "model": embedding_model,
        "provider": embedding_provider,
        "config_signature": config_signature,
        "batches": batches,
        "message_fetch_batch_size": message_fetch_batch_size,
        "embedding_request_batch_size": embedding_request_batch_size,
        "chat_metadata_count": metadata_result["chat_count"],
        "excluded_chat_count": len(store.excluded_chat_ids_for_embedding(policy=effective_policy)),
        "max_member_count": effective_policy.default_max_member_count,
        "policy_signature": effective_policy.signature,
        "policy_path": str(effective_policy.source_path),
        "query_profile_version": getattr(client, "query_profile_version", None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Kakao Live RAG semantic sidecar.")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--binary", default=str(DEFAULT_BINARY))
    parser.add_argument("--mode", choices=("rebuild", "update"), default="update")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-provider")
    parser.add_argument("--chunk-chars", type=int, default=DEFAULT_CHUNK_CHARS)
    parser.add_argument("--chunk-overlap", type=int, default=DEFAULT_CHUNK_OVERLAP)
    parser.add_argument("--max-member-count", type=int, default=DEFAULT_MAX_MEMBER_COUNT)
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--message-fetch-batch-size", type=int, default=DEFAULT_MESSAGE_FETCH_BATCH_SIZE)
    parser.add_argument("--embedding-request-batch-size", type=int, default=DEFAULT_EMBEDDING_REQUEST_BATCH_SIZE)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    store = LiveRAGStore(Path(args.db_path))
    client = ExternalEmbeddingClient(
        model=args.embedding_model,
        provider=args.embedding_provider,
    )
    effective_message_fetch_batch_size = args.batch_size or args.message_fetch_batch_size
    effective_embedding_request_batch_size = args.batch_size or args.embedding_request_batch_size
    policy = load_semantic_policy(Path(args.policy_path))
    try:
        payload = build_semantic_index(
            store,
            client,
            mode=args.mode,
            limit=args.limit,
            embedding_model=args.embedding_model,
            embedding_provider=args.embedding_provider,
            chunk_chars=args.chunk_chars,
            chunk_overlap=args.chunk_overlap,
            max_member_count=args.max_member_count,
            message_fetch_batch_size=effective_message_fetch_batch_size,
            embedding_request_batch_size=effective_embedding_request_batch_size,
            progress=args.progress,
            binary=args.binary,
            policy=policy,
        )
    except Exception as error:
        payload = {
            "status": "error",
            "stage": "build_index",
            "message": str(error),
            "mode": args.mode,
            "model": args.embedding_model,
            "provider": args.embedding_provider,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1) from error
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
