"""Unified CLI for Kakao Live RAG pipeline.

Orchestrates message ingestion, semantic indexing, querying,
and status reporting. Designed to be called from the Swift
`kakaocli rag` command via subprocess.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import queue
import time
from pathlib import Path
from typing import Any

try:
    from .env_loader import load_repo_env
    from .store import LiveRAGStore
    from .build_semantic_index import build_semantic_index, load_chat_metadata
    from .embedding_client import ExternalEmbeddingClient
    from .semantic_index import DEFAULT_EMBEDDING_MODEL
    from .policy import load_semantic_policy
    from .service_manager import (
        DEFAULT_BASE_URL,
        ensure_running,
        status as service_status,
    )
    from .query import post_json, render_text
except ImportError:
    from env_loader import load_repo_env
    from store import LiveRAGStore
    from build_semantic_index import build_semantic_index, load_chat_metadata
    from embedding_client import ExternalEmbeddingClient
    from semantic_index import DEFAULT_EMBEDDING_MODEL
    from policy import load_semantic_policy
    from service_manager import (
        DEFAULT_BASE_URL,
        ensure_running,
        status as service_status,
    )
    from query import post_json, render_text


load_repo_env()

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = REPO_ROOT / ".data" / "live_rag.sqlite3"
DEFAULT_BINARY = REPO_ROOT / ".build" / "release" / "kakaocli"

INGEST_BATCH_SIZE = 100
IDLE_TIMEOUT_SECONDS = 15


# ── helpers ──────────────────────────────────────────────────────────


def _resolve_binary(args: argparse.Namespace) -> str:
    binary = args.binary
    if not Path(binary).is_file():
        _err(f"Error: kakaocli binary not found at {binary}")
        _err("Run: swift build -c release")
        raise SystemExit(1)
    return binary


def _err(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


def _get_max_log_id(binary: str) -> int:
    """Run `kakaocli sync` (one-shot) to get the current max log ID."""
    result = subprocess.run(
        [binary, "sync"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"kakaocli sync failed: {stderr or 'unknown error'}")
    try:
        payload = json.loads(result.stdout.strip())
        return int(payload["max_log_id"])
    except (json.JSONDecodeError, KeyError, ValueError) as exc:
        raise RuntimeError(f"Failed to parse max_log_id from sync output: {result.stdout.strip()}") from exc


def _stream_sync_to_store(
    binary: str,
    store: LiveRAGStore,
    since_log_id: int,
    max_log_id: int,
    batch_size: int = INGEST_BATCH_SIZE,
) -> dict[str, Any]:
    """Stream NDJSON from `kakaocli sync --follow` into the store."""
    cmd = [
        binary, "sync", "--follow",
        "--since-log-id", str(since_log_id),
    ]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    line_queue: queue.Queue[str | None] = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line_queue.put(line)
        line_queue.put(None)  # sentinel

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    total_ingested = 0
    total_inserted = 0
    last_seen_log_id = 0
    batch: list[dict[str, Any]] = []

    def _flush() -> None:
        nonlocal total_ingested, total_inserted
        if not batch:
            return
        result = store.ingest_messages(batch, source="rag-cli")
        total_ingested += result["accepted"]
        total_inserted += result["inserted"]
        batch.clear()
        pct = min(100, int(last_seen_log_id / max(max_log_id, 1) * 100)) if last_seen_log_id else 0
        _err(f"\r  Ingested {total_ingested} messages ({pct}%)")

    try:
        last_activity = time.monotonic()
        while True:
            try:
                line = line_queue.get(timeout=1.0)
            except queue.Empty:
                # Check idle timeout
                if time.monotonic() - last_activity > IDLE_TIMEOUT_SECONDS:
                    current = store.last_ingested_log_id() or 0
                    if current >= max_log_id:
                        break
                    # Still waiting — extend timeout
                    last_activity = time.monotonic()
                continue

            if line is None:
                # sync process ended
                break

            last_activity = time.monotonic()
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(msg, dict) or "log_id" not in msg:
                continue

            batch.append(msg)
            last_seen_log_id = msg["log_id"]
            if len(batch) >= batch_size:
                _flush()

            # Check if we've caught up
            current = store.last_ingested_log_id() or 0
            if current >= max_log_id:
                break

        # Flush remaining
        _flush()

    finally:
        # Clean up sync process
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        _err("")  # newline after progress

    return {
        "ingested": total_ingested,
        "inserted": total_inserted,
    }


# ── subcommands ──────────────────────────────────────────────────────


def cmd_init(args: argparse.Namespace) -> int:
    binary = _resolve_binary(args)
    store = LiveRAGStore(Path(args.db_path))
    do_semantic = not args.no_semantic

    # Preflight: check binary works
    _err("[preflight] kakaocli sync 확인 중...")
    try:
        max_log_id = _get_max_log_id(binary)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        _err(f"Error: {exc}")
        return 1
    _err(f"  max_log_id = {max_log_id}")

    # Preflight: HF_TOKEN (only if semantic)
    if do_semantic and not os.environ.get("HF_TOKEN"):
        _err("Error: HF_TOKEN is required for semantic indexing.")
        _err("Set it in .env or export HF_TOKEN=...")
        _err("Or use --no-semantic to skip embedding.")
        return 1

    # Check existing data
    existing_stats = store.stats()
    if existing_stats.get("message_count", 0) > 0 and not args.force:
        _err(
            f"Error: Store already has {existing_stats['message_count']} messages. "
            f"Use --force to re-init, or use `kakaocli rag update`."
        )
        return 1

    # Ensure .data/ directory exists
    Path(args.db_path).parent.mkdir(parents=True, exist_ok=True)

    # Step 1: Ingest messages
    steps = 3 if do_semantic else 2
    _err(f"[1/{steps}] 메시지 동기화 중...")
    sync_result = _stream_sync_to_store(
        binary=binary,
        store=store,
        since_log_id=1,
        max_log_id=max_log_id,
    )

    # Step 2: Semantic index
    semantic_result: dict[str, Any] | None = None
    if do_semantic:
        _err(f"[2/{steps}] 시맨틱 인덱스 구축 중...")
        client = ExternalEmbeddingClient(model=args.embedding_model)
        policy = load_semantic_policy()
        try:
            semantic_result = build_semantic_index(
                store,
                client,
                mode="rebuild",
                limit=None,
                embedding_model=args.embedding_model,
                embedding_provider=None,
                progress=True,
                binary=binary,
                policy=policy,
            )
        except Exception as exc:
            _err(f"Warning: Semantic index build failed: {exc}")
            _err("Messages were ingested successfully. Run `kakaocli rag update` to retry.")
            semantic_result = {"status": "error", "message": str(exc)}

    # Done
    final_step = steps
    _err(f"[{final_step}/{steps}] 완료")

    summary = {
        "status": "ok",
        "command": "init",
        "sync": sync_result,
        "semantic": semantic_result,
        "store_stats": store.stats(),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    binary = _resolve_binary(args)
    store = LiveRAGStore(Path(args.db_path))
    do_semantic = not args.no_semantic

    checkpoint = store.last_ingested_log_id()
    if checkpoint is None:
        _err("Error: No existing data. Run `kakaocli rag init` first.")
        return 1

    _err("[preflight] kakaocli sync 확인 중...")
    try:
        max_log_id = _get_max_log_id(binary)
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        _err(f"Error: {exc}")
        return 1
    _err(f"  checkpoint = {checkpoint}, max_log_id = {max_log_id}")

    sync_result: dict[str, Any] | None = None
    if checkpoint >= max_log_id:
        _err("메시지가 이미 최신 상태입니다.")
    else:
        _err("[1/2] 증분 동기화 중...")
        sync_result = _stream_sync_to_store(
            binary=binary,
            store=store,
            since_log_id=checkpoint,
            max_log_id=max_log_id,
        )

    semantic_result: dict[str, Any] | None = None
    if do_semantic:
        if not os.environ.get("HF_TOKEN"):
            _err("Warning: HF_TOKEN not set. Skipping semantic index update.")
        else:
            _err("[2/2] 시맨틱 인덱스 업데이트 중...")
            client = ExternalEmbeddingClient(model=DEFAULT_EMBEDDING_MODEL)
            policy = load_semantic_policy()
            try:
                semantic_result = build_semantic_index(
                    store,
                    client,
                    mode="update",
                    limit=None,
                    embedding_model=DEFAULT_EMBEDDING_MODEL,
                    embedding_provider=None,
                    progress=True,
                    binary=binary,
                    policy=policy,
                )
            except Exception as exc:
                _err(f"Warning: Semantic index update failed: {exc}")
                semantic_result = {"status": "error", "message": str(exc)}

    summary = {
        "status": "ok",
        "command": "update",
        "sync": sync_result,
        "semantic": semantic_result,
        "store_stats": store.stats(),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_query(args: argparse.Namespace) -> int:
    query_text = (args.query_text_opt or args.query_text or "").strip()
    if not query_text:
        _err("Error: A query string is required.")
        return 1

    store = LiveRAGStore(Path(args.db_path))
    if (store.last_ingested_log_id() or 0) == 0:
        _err("Error: No data in store. Run `kakaocli rag init` first.")
        return 1

    _err("서비스 확인 중...")
    try:
        binary = Path(args.binary)
        ensure_running(
            base_url=DEFAULT_BASE_URL,
            db_path=Path(args.db_path),
            binary=binary,
        )
    except Exception as exc:
        _err(f"Error: Failed to start service: {exc}")
        return 1

    try:
        payload = post_json(
            f"{DEFAULT_BASE_URL.rstrip('/')}/retrieve",
            {
                "query": query_text,
                "mode": args.mode,
                "limit": args.limit,
            },
        )
    except Exception as exc:
        _err(f"Error: Query failed: {exc}")
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(payload))
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    store_stats: dict[str, Any] = {}
    try:
        store = LiveRAGStore(Path(args.db_path))
        store_stats = store.stats()
    except Exception as exc:
        store_stats = {"error": str(exc)}

    svc_status: dict[str, Any] = {}
    try:
        svc_status = service_status(base_url=DEFAULT_BASE_URL)
    except Exception as exc:
        svc_status = {"error": str(exc)}

    result = {
        "store": store_stats,
        "service": svc_status,
        "db_path": args.db_path,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        _print_status_text(result)
    return 0


def _print_status_text(result: dict[str, Any]) -> None:
    store = result.get("store", {})
    svc = result.get("service", {})

    print(f"DB path:    {result.get('db_path', '(unknown)')}")
    print(f"Messages:   {store.get('message_count', 0)}")
    print(f"Chats:      {store.get('chat_count', 0)}")
    print(f"Oldest:     {store.get('oldest_timestamp', '-')}")
    print(f"Newest:     {store.get('newest_timestamp', '-')}")
    print(f"Log ID:     {store.get('last_ingested_log_id', '-')}")

    sem_chunks = store.get("semantic_chunk_count", 0)
    sem_msgs = store.get("semantic_message_count", 0)
    sem_model = store.get("semantic_embedding_model", "-")
    print(f"Semantic:   {sem_chunks} chunks / {sem_msgs} messages (model: {sem_model})")

    health = svc.get("health")
    if health and health.get("status") == "ok":
        print(f"Service:    running (loaded: {svc.get('loaded', False)})")
    else:
        print(f"Service:    stopped (loaded: {svc.get('loaded', False)})")


# ── main ─────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(prog="kakaocli rag")
    parser.add_argument("--binary", default=str(DEFAULT_BINARY))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))

    subparsers = parser.add_subparsers(dest="command")

    # init
    p_init = subparsers.add_parser("init", help="Full history ingest + semantic index build")
    p_init.add_argument("--force", action="store_true", help="Re-run even if store already has data")
    p_init.add_argument("--no-semantic", action="store_true", help="Skip semantic embedding")
    p_init.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)

    # update
    p_update = subparsers.add_parser("update", help="Incremental sync + index update")
    p_update.add_argument("--no-semantic", action="store_true", help="Skip semantic embedding")

    # query
    p_query = subparsers.add_parser("query", help="Query the RAG store")
    p_query.add_argument("query_text", nargs="?")
    p_query.add_argument("--query-text", dest="query_text_opt")
    p_query.add_argument("--mode", choices=("lexical", "semantic", "hybrid"), default="hybrid")
    p_query.add_argument("--limit", type=int, default=8)
    p_query.add_argument("--json", action="store_true")

    # status
    p_status = subparsers.add_parser("status", help="Show RAG store and service status")
    p_status.add_argument("--json", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    dispatch = {
        "init": cmd_init,
        "update": cmd_update,
        "query": cmd_query,
        "status": cmd_status,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
