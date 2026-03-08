"""Local query entrypoint for Kakao Live RAG.

Ensures the background service is running, then submits
retrieval requests and prints evidence-ready results.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .service_manager import DEFAULT_BASE_URL, DEFAULT_BINARY, DEFAULT_DB_PATH, ensure_running
except ImportError:
    from service_manager import DEFAULT_BASE_URL, DEFAULT_BINARY, DEFAULT_DB_PATH, ensure_running


def post_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30.0) as response:
        return json.loads(response.read().decode("utf-8"))


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"query: {payload['query']}",
        f"mode: {payload.get('actual_mode', payload.get('mode', 'lexical'))}",
        f"hits: {len(payload['hits'])}",
    ]
    if payload.get("requested_mode") and payload.get("requested_mode") != payload.get("actual_mode", payload.get("mode")):
        lines.insert(2, f"requested_mode: {payload['requested_mode']}")
        lines.insert(3, f"fallback_reason: {payload.get('fallback_reason', 'semantic_unavailable')}")
    for index, hit in enumerate(payload["hits"], start=1):
        message = hit["message"]
        lines.append(
            f"{index}. [{message.get('chat_name') or '(unknown)'}] "
            f"{message.get('sender') or '(unknown)'} @ {message.get('timestamp')}"
        )
        lines.append(f"   text: {message.get('text') or ''}")
        for context in hit.get("context_before", []):
            lines.append(
                f"   before: {(context.get('sender') or '(unknown)')}: {context.get('text') or ''}"
            )
        for context in hit.get("context_after", []):
            lines.append(
                f"   after: {(context.get('sender') or '(unknown)')}: {context.get('text') or ''}"
            )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Query the local Kakao Live RAG service.")
    parser.add_argument("query", nargs="?")
    parser.add_argument("--query-text")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--binary", default=str(DEFAULT_BINARY))
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--mode", choices=("lexical", "semantic", "hybrid"), default="hybrid")
    parser.add_argument("--semantic-top-k", type=int, default=24)
    parser.add_argument("--rerank", choices=("off", "on", "auto"), default="auto")
    parser.add_argument("--rerank-top-n", type=int, default=12)
    parser.add_argument("--service-mode", choices=("follow", "server-only", "off"), default="follow")
    parser.add_argument("--chat-id", type=int)
    parser.add_argument("--speaker")
    parser.add_argument("--since-days", type=float)
    parser.add_argument("--context-before", type=int, default=2)
    parser.add_argument("--context-after", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    query = (args.query_text or args.query or "").strip()
    if not query:
        raise SystemExit("A query string is required.")

    if args.service_mode != "off":
        ensure_running(
            base_url=args.base_url,
            db_path=Path(args.db_path),
            binary=Path(args.binary),
            timeout=args.timeout,
            service_mode=args.service_mode,
        )

    payload = post_json(
        f"{args.base_url.rstrip('/')}/retrieve",
        {
            "query": query,
            "mode": args.mode,
            "limit": args.limit,
            "semantic_top_k": args.semantic_top_k,
            "rerank": args.rerank,
            "rerank_top_n": args.rerank_top_n,
            "chat_id": args.chat_id,
            "speaker": args.speaker,
            "since_days": args.since_days,
            "context_before": args.context_before,
            "context_after": args.context_after,
        },
    )
    payload["service_mode"] = args.service_mode

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print(render_text(payload))


if __name__ == "__main__":
    try:
        main()
    except urllib.error.URLError as error:
        sys.stderr.write(f"Failed to query live RAG: {error}\n")
        raise SystemExit(1) from error
