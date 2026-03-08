"""Run kakaocli sync against the local Live RAG webhook.

Resumes from the store checkpoint when no explicit
`--since-log-id` is provided.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

try:
    from .store import LiveRAGStore
except ImportError:
    from store import LiveRAGStore


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "live_rag.sqlite3"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run kakaocli sync against the local live RAG webhook.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--binary",
        default=str(Path(__file__).resolve().parents[2] / ".build" / "release" / "kakaocli"),
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--since-log-id", type=int)
    args = parser.parse_args()

    since_log_id = args.since_log_id
    if since_log_id is None:
        store = LiveRAGStore(Path(args.db_path))
        since_log_id = store.last_ingested_log_id()

    command = [
        args.binary,
        "sync",
        "--follow",
        "--webhook",
        f"{args.base_url.rstrip('/')}/kakao",
        "--interval",
        str(args.interval),
    ]
    if since_log_id is not None:
        command.extend(["--since-log-id", str(since_log_id)])

    raise SystemExit(subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    main()
