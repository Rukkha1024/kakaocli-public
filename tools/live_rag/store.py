"""SQLite store for Kakao Live RAG.

Keeps canonical messages, lexical search state,
and semantic sidecar data in one local database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np

try:
    from .policy import SemanticPolicy
    from .semantic_index import reciprocal_rank_fuse
except ImportError:
    from policy import SemanticPolicy
    from semantic_index import reciprocal_rank_fuse


SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS messages (
    log_id INTEGER PRIMARY KEY,
    event_type TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_name TEXT,
    sender_id INTEGER NOT NULL,
    sender TEXT,
    text TEXT,
    message_type INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    is_from_me INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_timestamp
    ON messages (chat_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_messages_sender_timestamp
    ON messages (sender, timestamp);

CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    sender,
    chat_name,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    batch_size INTEGER NOT NULL,
    inserted_count INTEGER NOT NULL,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS live_rag_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS chat_metadata (
    chat_id INTEGER PRIMARY KEY,
    chat_name TEXT,
    member_count INTEGER NOT NULL,
    chat_type TEXT,
    raw_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_chat_metadata_member_count
    ON chat_metadata (member_count, chat_type);

CREATE TABLE IF NOT EXISTS semantic_chunks (
    chunk_id TEXT PRIMARY KEY,
    log_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_name TEXT,
    sender TEXT,
    timestamp TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    embedding_model TEXT NOT NULL,
    embedding_provider TEXT,
    config_signature TEXT NOT NULL,
    source_signature TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_semantic_chunks_log_id
    ON semantic_chunks (log_id);

CREATE INDEX IF NOT EXISTS idx_semantic_chunks_config
    ON semantic_chunks (config_signature, timestamp);

CREATE INDEX IF NOT EXISTS idx_semantic_chunks_chat_sender
    ON semantic_chunks (chat_id, sender, timestamp);
"""

SEMANTIC_STATE_KEYS = (
    "semantic_config_signature",
    "semantic_embedding_model",
    "semantic_embedding_provider",
    "semantic_chunk_chars",
    "semantic_chunk_overlap",
    "semantic_max_member_count",
    "semantic_last_indexed_log_id",
    "semantic_policy_signature",
    "semantic_policy_path",
    "semantic_query_profile_version",
    "semantic_message_fetch_batch_size",
    "semantic_embedding_request_batch_size",
)


class LiveRAGStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._ensure_fts_table(connection)
            self._ensure_checkpoint_state(connection)

    def _ensure_fts_table(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            """
            SELECT sql
            FROM sqlite_master
            WHERE type = 'table' AND name = 'messages_fts'
            """
        ).fetchone()
        sql = row["sql"] if row is not None else None
        if sql and "content=''" not in sql:
            return

        connection.execute("DROP TABLE IF EXISTS messages_fts")
        connection.execute(
            """
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                text,
                sender,
                chat_name,
                tokenize='unicode61 remove_diacritics 2'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO messages_fts (rowid, text, sender, chat_name)
            SELECT
                log_id,
                COALESCE(text, ''),
                COALESCE(sender, ''),
                COALESCE(chat_name, '')
            FROM messages
            """
        )
        connection.commit()

    def ingest_messages(self, messages: list[dict[str, Any]], source: str = "webhook") -> dict[str, int]:
        normalized = [self._normalize_message(message) for message in messages]
        if not normalized:
            return {"accepted": 0, "inserted": 0}

        log_ids = [message["log_id"] for message in normalized]
        with self._connect() as connection:
            current_checkpoint = self._get_int_state(connection, "last_ingested_log_id")
            existing = {
                row["log_id"]
                for row in connection.execute(
                    f"SELECT log_id FROM messages WHERE log_id IN ({','.join('?' for _ in log_ids)})",
                    log_ids,
                )
            }
            for message in normalized:
                connection.execute(
                    """
                    INSERT INTO messages (
                        log_id,
                        event_type,
                        chat_id,
                        chat_name,
                        sender_id,
                        sender,
                        text,
                        message_type,
                        timestamp,
                        is_from_me,
                        raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(log_id) DO UPDATE SET
                        event_type = excluded.event_type,
                        chat_id = excluded.chat_id,
                        chat_name = excluded.chat_name,
                        sender_id = excluded.sender_id,
                        sender = excluded.sender,
                        text = excluded.text,
                        message_type = excluded.message_type,
                        timestamp = excluded.timestamp,
                        is_from_me = excluded.is_from_me,
                        raw_json = excluded.raw_json
                    """,
                    (
                        message["log_id"],
                        message["type"],
                        message["chat_id"],
                        message.get("chat_name"),
                        message["sender_id"],
                        message.get("sender"),
                        message.get("text"),
                        message["message_type"],
                        message["timestamp"],
                        int(message["is_from_me"]),
                        json.dumps(message, ensure_ascii=False, sort_keys=True),
                    ),
                )
                connection.execute("DELETE FROM messages_fts WHERE rowid = ?", (message["log_id"],))
                connection.execute(
                    """
                    INSERT INTO messages_fts (rowid, text, sender, chat_name)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        message["log_id"],
                        message.get("text") or "",
                        message.get("sender") or "",
                        message.get("chat_name") or "",
                    ),
                )

            inserted = len([log_id for log_id in log_ids if log_id not in existing])
            connection.execute(
                """
                INSERT INTO ingest_runs (source, batch_size, inserted_count)
                VALUES (?, ?, ?)
                """,
                (source, len(normalized), inserted),
            )
            next_checkpoint = max(log_ids) if current_checkpoint is None else max(current_checkpoint, max(log_ids))
            self._set_state(connection, "last_ingested_log_id", str(next_checkpoint))
            self._set_state(connection, "last_ingest_source", source)
            connection.commit()

        return {"accepted": len(normalized), "inserted": inserted}

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS message_count,
                    COUNT(DISTINCT chat_id) AS chat_count,
                    MIN(timestamp) AS oldest_timestamp,
                    MAX(timestamp) AS newest_timestamp
                FROM messages
                """
            ).fetchone()
            stats = dict(row) if row is not None else {}
            stats["last_ingested_log_id"] = self._get_int_state(connection, "last_ingested_log_id")
            stats["last_ingest_source"] = self._get_state(connection, "last_ingest_source")
            semantic_stats = connection.execute(
                """
                SELECT
                    COUNT(*) AS semantic_chunk_count,
                    COUNT(DISTINCT log_id) AS semantic_message_count
                FROM semantic_chunks
                """
            ).fetchone()
            if semantic_stats is not None:
                stats.update(dict(semantic_stats))
            stats["semantic_config_signature"] = self._get_state(connection, "semantic_config_signature")
            stats["semantic_embedding_model"] = self._get_state(connection, "semantic_embedding_model")
            stats["semantic_embedding_provider"] = self._get_state(connection, "semantic_embedding_provider")
            stats["semantic_max_member_count"] = self._get_int_state(connection, "semantic_max_member_count")
            stats["semantic_last_indexed_log_id"] = self._get_int_state(connection, "semantic_last_indexed_log_id")
            stats["semantic_policy_signature"] = self._get_state(connection, "semantic_policy_signature")
            stats["semantic_policy_path"] = self._get_state(connection, "semantic_policy_path")
            stats["semantic_query_profile_version"] = self._get_state(connection, "semantic_query_profile_version")
            stats["semantic_message_fetch_batch_size"] = self._get_int_state(
                connection,
                "semantic_message_fetch_batch_size",
            )
            stats["semantic_embedding_request_batch_size"] = self._get_int_state(
                connection,
                "semantic_embedding_request_batch_size",
            )
            metadata_stats = connection.execute(
                """
                SELECT
                    COUNT(*) AS chat_metadata_count,
                    SUM(CASE WHEN member_count > 30 THEN 1 ELSE 0 END) AS excluded_chat_count
                FROM chat_metadata
                """
            ).fetchone()
            if metadata_stats is not None:
                stats.update(dict(metadata_stats))
            return stats

    def upsert_chat_metadata(self, chats: list[dict[str, Any]]) -> dict[str, int]:
        if not chats:
            return {"chat_count": 0}

        normalized = [self._normalize_chat_metadata(chat) for chat in chats]
        with self._connect() as connection:
            for chat in normalized:
                connection.execute(
                    """
                    INSERT INTO chat_metadata (
                        chat_id,
                        chat_name,
                        member_count,
                        chat_type,
                        raw_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    ON CONFLICT(chat_id) DO UPDATE SET
                        chat_name = excluded.chat_name,
                        member_count = excluded.member_count,
                        chat_type = excluded.chat_type,
                        raw_json = excluded.raw_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        chat["chat_id"],
                        chat.get("chat_name"),
                        chat["member_count"],
                        chat.get("chat_type"),
                        json.dumps(chat["raw_json"], ensure_ascii=False, sort_keys=True),
                    ),
                )
            connection.commit()
        return {"chat_count": len(normalized)}

    def excluded_chat_ids_for_embedding(
        self,
        *,
        policy: SemanticPolicy | None = None,
        max_member_count: int | None = None,
    ) -> list[int]:
        effective_policy = self._coerce_policy(policy=policy, max_member_count=max_member_count)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT chat_id, member_count
                FROM chat_metadata
                ORDER BY chat_id ASC
                """
            ).fetchall()
        return [
            int(row["chat_id"])
            for row in rows
            if not effective_policy.is_chat_eligible(
                chat_id=int(row["chat_id"]),
                member_count=int(row["member_count"]),
            )
        ]

    def count_embedding_messages_missing_chat_metadata(
        self,
        *,
        after_log_id: int | None,
        limit: int | None,
    ) -> int:
        clauses = ["m.message_type = 1", "COALESCE(TRIM(m.text), '') != ''", "c.chat_id IS NULL"]
        params: list[Any] = []
        if after_log_id is not None:
            clauses.append("m.log_id > ?")
            params.append(after_log_id)
        limit_sql = ""
        if limit is not None:
            limit_sql = "LIMIT ?"
            params.append(limit)

        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS missing_count
                FROM (
                    SELECT m.log_id
                    FROM messages AS m
                    LEFT JOIN chat_metadata AS c
                        ON c.chat_id = m.chat_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY m.log_id ASC
                    {limit_sql}
                )
                """,
                params,
            ).fetchone()
        return 0 if row is None else int(row["missing_count"])

    def list_messages(
        self,
        *,
        limit: int = 50,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if speaker:
            clauses.append("sender = ?")
            params.append(speaker)
        if since_days is not None:
            cutoff = self._utc_now() - timedelta(days=since_days)
            clauses.append("timestamp >= ?")
            params.append(self._isoformat(cutoff))

        where_clause = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    event_type AS type,
                    log_id,
                    chat_id,
                    chat_name,
                    sender_id,
                    sender,
                    text,
                    message_type,
                    timestamp,
                    is_from_me
                FROM messages
                {where_clause}
                ORDER BY timestamp DESC, log_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [
                {
                    "type": row["type"],
                    "log_id": row["log_id"],
                    "chat_id": row["chat_id"],
                    "chat_name": row["chat_name"],
                    "sender_id": row["sender_id"],
                    "sender": row["sender"],
                    "text": row["text"],
                    "message_type": row["message_type"],
                    "timestamp": row["timestamp"],
                    "is_from_me": bool(row["is_from_me"]),
                }
                for row in rows
            ]

    def iter_messages_for_embedding(
        self,
        after_log_id: int | None,
        limit: int | None,
        *,
        policy: SemanticPolicy | None = None,
        max_member_count: int | None = None,
    ) -> list[dict[str, Any]]:
        effective_policy = self._coerce_policy(policy=policy, max_member_count=max_member_count)
        clauses = [
            "m.message_type = 1",
            "COALESCE(TRIM(m.text), '') != ''",
        ]
        params: list[Any] = []
        cursor = after_log_id
        collected: list[dict[str, Any]] = []
        scan_limit = max(256, (limit or 256) * 4)

        with self._connect() as connection:
            while True:
                scan_clauses = list(clauses)
                scan_params = list(params)
                if cursor is not None:
                    scan_clauses.append("m.log_id > ?")
                    scan_params.append(cursor)
                scan_params.append(scan_limit)
                rows = connection.execute(
                    f"""
                    SELECT m.*, c.member_count
                    FROM messages AS m
                    INNER JOIN chat_metadata AS c
                        ON c.chat_id = m.chat_id
                    WHERE {' AND '.join(scan_clauses)}
                    ORDER BY m.log_id ASC
                    LIMIT ?
                    """,
                    scan_params,
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    if effective_policy.is_chat_eligible(
                        chat_id=int(row["chat_id"]),
                        member_count=int(row["member_count"]),
                    ):
                        collected.append(self._serialize_row(row))
                        if limit is not None and len(collected) >= limit:
                            return collected
                cursor = int(rows[-1]["log_id"])
                if len(rows) < scan_limit:
                    break
        return collected

    def clear_semantic_index(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM semantic_chunks")
            for key in SEMANTIC_STATE_KEYS:
                self._delete_state(connection, key)
            connection.commit()

    def upsert_semantic_chunks(self, chunks: list[dict[str, Any]]) -> dict[str, int]:
        if not chunks:
            return {"messages_indexed": 0, "chunks_indexed": 0}

        log_ids = sorted({int(chunk["log_id"]) for chunk in chunks})
        with self._connect() as connection:
            connection.execute(
                f"DELETE FROM semantic_chunks WHERE log_id IN ({','.join('?' for _ in log_ids)})",
                log_ids,
            )
            for chunk in chunks:
                connection.execute(
                    """
                    INSERT INTO semantic_chunks (
                        chunk_id,
                        log_id,
                        chat_id,
                        chat_name,
                        sender,
                        timestamp,
                        chunk_index,
                        chunk_text,
                        vector_json,
                        embedding_model,
                        embedding_provider,
                        config_signature,
                        source_signature,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                    """,
                    (
                        chunk["chunk_id"],
                        int(chunk["log_id"]),
                        int(chunk["chat_id"]),
                        chunk.get("chat_name"),
                        chunk.get("sender"),
                        str(chunk["timestamp"]),
                        int(chunk["chunk_index"]),
                        str(chunk["chunk_text"]),
                        json.dumps(chunk["vector"], ensure_ascii=False, separators=(",", ":")),
                        str(chunk["embedding_model"]),
                        chunk.get("embedding_provider"),
                        str(chunk["config_signature"]),
                        str(chunk["source_signature"]),
                    ),
                )
            connection.commit()

        return {"messages_indexed": len(log_ids), "chunks_indexed": len(chunks)}

    def set_runtime_state(self, key: str, value: str) -> None:
        with self._connect() as connection:
            self._set_state(connection, key, value)
            connection.commit()

    def get_semantic_settings(self) -> dict[str, Any] | None:
        with self._connect() as connection:
            config_signature = self._get_state(connection, "semantic_config_signature")
            embedding_model = self._get_state(connection, "semantic_embedding_model")
            if not config_signature or not embedding_model:
                return None
            return {
                "config_signature": config_signature,
                "embedding_model": embedding_model,
                "embedding_provider": self._empty_to_none(
                    self._get_state(connection, "semantic_embedding_provider")
                ),
                "chunk_chars": self._get_int_state(connection, "semantic_chunk_chars"),
                "chunk_overlap": self._get_int_state(connection, "semantic_chunk_overlap"),
                "max_member_count": self._get_int_state(connection, "semantic_max_member_count"),
                "last_indexed_log_id": self._get_int_state(connection, "semantic_last_indexed_log_id"),
                "policy_signature": self._get_state(connection, "semantic_policy_signature"),
                "policy_path": self._get_state(connection, "semantic_policy_path"),
                "query_profile_version": self._get_state(connection, "semantic_query_profile_version"),
                "message_fetch_batch_size": self._get_int_state(connection, "semantic_message_fetch_batch_size"),
                "embedding_request_batch_size": self._get_int_state(
                    connection,
                    "semantic_embedding_request_batch_size",
                ),
            }

    def retrieve(
        self,
        *,
        query: str,
        limit: int = 8,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
        context_before: int = 2,
        context_after: int = 2,
    ) -> list[dict[str, Any]]:
        return self.retrieve_lexical(
            query=query,
            limit=limit,
            chat_id=chat_id,
            speaker=speaker,
            since_days=since_days,
            context_before=context_before,
            context_after=context_after,
        )

    def retrieve_lexical(
        self,
        *,
        query: str,
        limit: int = 8,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
        context_before: int = 2,
        context_after: int = 2,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []

        try:
            hits = self._fts_hits(
                query=query,
                limit=limit,
                chat_id=chat_id,
                speaker=speaker,
                since_days=since_days,
                context_before=context_before,
                context_after=context_after,
            )
        except sqlite3.OperationalError:
            hits = self._like_hits(
                query=query,
                limit=limit,
                chat_id=chat_id,
                speaker=speaker,
                since_days=since_days,
                context_before=context_before,
                context_after=context_after,
            )

        return [self._serialize_hit(hit) for hit in hits]

    def retrieve_semantic(
        self,
        *,
        query_vector: list[float],
        limit: int = 8,
        semantic_top_k: int = 24,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
        context_before: int = 2,
        context_after: int = 2,
        config_signature: str | None = None,
    ) -> list[dict[str, Any]]:
        matches = self.semantic_search(
            query_vector=query_vector,
            limit=semantic_top_k,
            chat_id=chat_id,
            speaker=speaker,
            since_days=since_days,
            config_signature=config_signature,
        )
        if not matches:
            return []

        with self._connect() as connection:
            hits = [
                self._expand_message_hit(
                    connection,
                    log_id=int(match["log_id"]),
                    score=float(match["score"]),
                    context_before=context_before,
                    context_after=context_after,
                    hit_fields={
                        "semantic_score": float(match["score"]),
                        "retrieval_sources": ["semantic"],
                        "matched_chunk_text": match.get("chunk_text"),
                        "matched_chunk_id": match.get("chunk_id"),
                        "semantic_config_signature": match.get("config_signature"),
                        "embedding_model": match.get("embedding_model"),
                        "embedding_provider": match.get("embedding_provider"),
                    },
                )
                for match in matches[:limit]
            ]
        return [self._serialize_hit(hit) for hit in hits]

    def retrieve_hybrid(
        self,
        *,
        query: str,
        semantic_query_vector: list[float],
        limit: int = 8,
        semantic_top_k: int = 24,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
        context_before: int = 2,
        context_after: int = 2,
        config_signature: str | None = None,
    ) -> list[dict[str, Any]]:
        lexical_hits = self.retrieve_lexical(
            query=query,
            limit=limit,
            chat_id=chat_id,
            speaker=speaker,
            since_days=since_days,
            context_before=context_before,
            context_after=context_after,
        )
        semantic_hits = self.retrieve_semantic(
            query_vector=semantic_query_vector,
            limit=limit,
            semantic_top_k=semantic_top_k,
            chat_id=chat_id,
            speaker=speaker,
            since_days=since_days,
            context_before=context_before,
            context_after=context_after,
            config_signature=config_signature,
        )
        return reciprocal_rank_fuse(lexical_hits, semantic_hits, limit=limit)

    def semantic_search(
        self,
        query_vector: list[float],
        limit: int,
        chat_id: int | None = None,
        speaker: str | None = None,
        since_days: float | None = None,
        config_signature: str | None = None,
    ) -> list[dict[str, Any]]:
        signature = config_signature
        with self._connect() as connection:
            if signature is None:
                signature = self._get_state(connection, "semantic_config_signature")
            if not signature:
                return []

            clauses = ["config_signature = ?"]
            params: list[Any] = [signature]
            if chat_id is not None:
                clauses.append("chat_id = ?")
                params.append(chat_id)
            if speaker:
                clauses.append("sender = ?")
                params.append(speaker)
            if since_days is not None:
                cutoff = self._utc_now() - timedelta(days=since_days)
                clauses.append("timestamp >= ?")
                params.append(self._isoformat(cutoff))

            rows = connection.execute(
                f"""
                SELECT *
                FROM semantic_chunks
                WHERE {' AND '.join(clauses)}
                ORDER BY log_id ASC, chunk_index ASC
                """,
                params,
            ).fetchall()

        if not rows:
            return []

        query_array = self._normalize_vector(query_vector)
        best_by_log_id: dict[int, dict[str, Any]] = {}
        for row in rows:
            vector = self._parse_vector(row["vector_json"])
            if vector.size != query_array.size:
                continue
            score = float(np.dot(query_array, vector))
            log_id = int(row["log_id"])
            current = best_by_log_id.get(log_id)
            if current is None or score > float(current["score"]):
                best_by_log_id[log_id] = {
                    "log_id": log_id,
                    "chat_id": int(row["chat_id"]),
                    "sender": row["sender"],
                    "timestamp": row["timestamp"],
                    "score": score,
                    "chunk_text": row["chunk_text"],
                    "chunk_id": row["chunk_id"],
                    "config_signature": row["config_signature"],
                    "embedding_model": row["embedding_model"],
                    "embedding_provider": row["embedding_provider"],
                }

        ranked = sorted(
            best_by_log_id.values(),
            key=lambda item: (-float(item["score"]), item["timestamp"], item["log_id"]),
        )
        return ranked[:limit]

    def last_ingested_log_id(self) -> int | None:
        with self._connect() as connection:
            return self._get_int_state(connection, "last_ingested_log_id")

    def _fts_hits(
        self,
        *,
        query: str,
        limit: int,
        chat_id: int | None,
        speaker: str | None,
        since_days: float | None,
        context_before: int,
        context_after: int,
    ) -> list[dict[str, Any]]:
        clauses = ["messages_fts MATCH ?"]
        params: list[Any] = [query]

        if chat_id is not None:
            clauses.append("m.chat_id = ?")
            params.append(chat_id)
        if speaker:
            clauses.append("m.sender = ?")
            params.append(speaker)
        if since_days is not None:
            cutoff = self._utc_now() - timedelta(days=since_days)
            clauses.append("m.timestamp >= ?")
            params.append(self._isoformat(cutoff))
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT
                    m.*,
                    bm25(messages_fts) AS score
                FROM messages_fts
                JOIN messages AS m ON m.log_id = messages_fts.rowid
                WHERE {' AND '.join(clauses)}
                ORDER BY score, m.timestamp DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [
                self._expand_hit(
                    connection,
                    row,
                    context_before=context_before,
                    context_after=context_after,
                )
                for row in rows
            ]

    def _like_hits(
        self,
        *,
        query: str,
        limit: int,
        chat_id: int | None,
        speaker: str | None,
        since_days: float | None,
        context_before: int,
        context_after: int,
    ) -> list[dict[str, Any]]:
        clauses = ["COALESCE(text, '') LIKE ?"]
        params: list[Any] = [f"%{query}%"]

        if chat_id is not None:
            clauses.append("chat_id = ?")
            params.append(chat_id)
        if speaker:
            clauses.append("sender = ?")
            params.append(speaker)
        if since_days is not None:
            cutoff = self._utc_now() - timedelta(days=since_days)
            clauses.append("timestamp >= ?")
            params.append(self._isoformat(cutoff))
        params.append(limit)

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *, 0.0 AS score
                FROM messages
                WHERE {' AND '.join(clauses)}
                ORDER BY timestamp DESC, log_id DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            return [
                self._expand_hit(
                    connection,
                    row,
                    context_before=context_before,
                    context_after=context_after,
                )
                for row in rows
            ]

    def _expand_hit(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        context_before: int,
        context_after: int,
    ) -> dict[str, Any]:
        return self._expand_message_hit(
            connection,
            log_id=int(row["log_id"]),
            score=float(row["score"]),
            context_before=context_before,
            context_after=context_after,
            row=row,
            hit_fields={
                "lexical_score": float(row["score"]),
                "retrieval_sources": ["lexical"],
            },
        )

    def _expand_message_hit(
        self,
        connection: sqlite3.Connection,
        *,
        log_id: int,
        score: float,
        context_before: int,
        context_after: int,
        row: sqlite3.Row | None = None,
        hit_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message_row = row
        if message_row is None:
            message_row = connection.execute(
                "SELECT * FROM messages WHERE log_id = ?",
                (log_id,),
            ).fetchone()
        if message_row is None:
            raise LookupError(f"Missing message row for log_id={log_id}")

        current_log_id = int(message_row["log_id"])
        chat_id = int(message_row["chat_id"])
        before_rows = connection.execute(
            """
            SELECT *
            FROM messages
            WHERE chat_id = ? AND log_id < ?
            ORDER BY log_id DESC
            LIMIT ?
            """,
            (chat_id, current_log_id, context_before),
        ).fetchall()
        after_rows = connection.execute(
            """
            SELECT *
            FROM messages
            WHERE chat_id = ? AND log_id > ?
            ORDER BY log_id ASC
            LIMIT ?
            """,
            (chat_id, current_log_id, context_after),
        ).fetchall()

        payload = {
            "score": score,
            "message": message_row,
            "context_before": list(reversed(before_rows)),
            "context_after": list(after_rows),
        }
        if hit_fields:
            payload.update(hit_fields)
        return payload

    def _serialize_hit(self, hit: dict[str, Any]) -> dict[str, Any]:
        return {
            "score": float(hit["score"]),
            "lexical_score": self._optional_float(hit.get("lexical_score")),
            "semantic_score": self._optional_float(hit.get("semantic_score")),
            "fusion_score": self._optional_float(hit.get("fusion_score")),
            "retrieval_sources": list(hit.get("retrieval_sources", [])),
            "matched_chunk_text": hit.get("matched_chunk_text"),
            "matched_chunk_id": hit.get("matched_chunk_id"),
            "semantic_config_signature": hit.get("semantic_config_signature"),
            "embedding_model": hit.get("embedding_model"),
            "embedding_provider": hit.get("embedding_provider"),
            "rerank_score": self._optional_float(hit.get("rerank_score")),
            "message": self._serialize_row(hit["message"]),
            "context_before": [self._serialize_row(row) for row in hit["context_before"]],
            "context_after": [self._serialize_row(row) for row in hit["context_after"]],
        }

    def _normalize_message(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": str(message.get("type", "message")),
            "log_id": int(message["log_id"]),
            "chat_id": int(message["chat_id"]),
            "chat_name": message.get("chat_name"),
            "sender_id": int(message.get("sender_id", 0)),
            "sender": message.get("sender"),
            "text": message.get("text"),
            "message_type": int(message.get("message_type", -1)),
            "timestamp": str(message["timestamp"]),
            "is_from_me": bool(message.get("is_from_me", False)),
        }

    def _normalize_chat_metadata(self, chat: dict[str, Any]) -> dict[str, Any]:
        return {
            "chat_id": int(chat["id"]),
            "chat_name": str(chat.get("display_name") or "").strip() or None,
            "member_count": int(chat.get("member_count", 0)),
            "chat_type": str(chat.get("type") or "").strip() or None,
            "raw_json": chat,
        }

    def _serialize_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "type": row["event_type"],
            "log_id": row["log_id"],
            "chat_id": row["chat_id"],
            "chat_name": row["chat_name"],
            "sender_id": row["sender_id"],
            "sender": row["sender"],
            "text": row["text"],
            "message_type": row["message_type"],
            "timestamp": row["timestamp"],
            "is_from_me": bool(row["is_from_me"]),
        }

    def _ensure_checkpoint_state(self, connection: sqlite3.Connection) -> None:
        if self._get_int_state(connection, "last_ingested_log_id") is not None:
            return
        max_log_id = self._max_log_id(connection)
        if max_log_id is not None:
            self._set_state(connection, "last_ingested_log_id", str(max_log_id))

    def _max_log_id(self, connection: sqlite3.Connection) -> int | None:
        row = connection.execute("SELECT MAX(log_id) AS max_log_id FROM messages").fetchone()
        if row is None or row["max_log_id"] is None:
            return None
        return int(row["max_log_id"])

    def _get_state(self, connection: sqlite3.Connection, key: str) -> str | None:
        row = connection.execute(
            "SELECT value FROM live_rag_state WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def _get_int_state(self, connection: sqlite3.Connection, key: str) -> int | None:
        value = self._get_state(connection, key)
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _set_state(self, connection: sqlite3.Connection, key: str, value: str) -> None:
        connection.execute(
            """
            INSERT INTO live_rag_state (key, value, updated_at)
            VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value),
        )

    def _delete_state(self, connection: sqlite3.Connection, key: str) -> None:
        connection.execute("DELETE FROM live_rag_state WHERE key = ?", (key,))

    @staticmethod
    def _normalize_vector(vector: list[float]) -> np.ndarray:
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1 or array.size == 0:
            raise ValueError("Expected a one-dimensional vector.")
        norm = float(np.linalg.norm(array))
        if norm == 0.0:
            raise ValueError("Expected a non-zero vector.")
        return array / norm

    @staticmethod
    def _parse_vector(raw_value: str) -> np.ndarray:
        return LiveRAGStore._normalize_vector(json.loads(raw_value))

    @staticmethod
    def _empty_to_none(value: str | None) -> str | None:
        if value is None or value == "":
            return None
        return value

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)

    @staticmethod
    def _coerce_policy(
        *,
        policy: SemanticPolicy | None,
        max_member_count: int | None,
    ) -> SemanticPolicy:
        if policy is not None:
            return policy
        effective_max = 30 if max_member_count is None else int(max_member_count)
        return SemanticPolicy(
            default_max_member_count=effective_max,
            allow_chat_ids=(),
            deny_chat_ids=(),
            chat_overrides={},
            signature=f"legacy-max-member-count-{effective_max}",
            source_path=Path("<legacy-inline>"),
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _isoformat(value: datetime) -> str:
        return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
