from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .store import LiveRAGStore
except ImportError:
    from store import LiveRAGStore


TYPE_MAP = {
    "text": 1,
    "photo": 2,
    "video": 3,
    "voice": 4,
    "sticker": 5,
    "file": 6,
    "location": 7,
    "system": 0,
    "unknown": -1,
}
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".data" / "live_rag.sqlite3"


def run_json(command: list[str]) -> Any:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def post_json(url: str, payload: list[dict[str, Any]]) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def normalize_message(message: dict[str, Any], chat_name: str | None) -> dict[str, Any]:
    message_type_name = str(message.get("type", "unknown"))
    return {
        "type": "message",
        "log_id": int(message["id"]),
        "chat_id": int(message["chat_id"]),
        "chat_name": chat_name,
        "sender_id": int(message.get("sender_id", 0)),
        "sender": message.get("sender"),
        "text": message.get("text"),
        "message_type": TYPE_MAP.get(message_type_name, -1),
        "timestamp": message["timestamp"],
        "is_from_me": bool(message.get("is_from_me", False)),
    }


def resolve_chats(binary: str, limit_chats: int) -> list[dict[str, Any]]:
    return run_json([binary, "chats", "--limit", str(limit_chats), "--json"])


def resolve_targets(
    *,
    binary: str,
    chat: str | None,
    chat_id: int | None,
    limit_chats: int,
) -> list[dict[str, Any]]:
    chats = resolve_chats(binary, limit_chats)
    if chat_id is not None:
        return [item for item in chats if int(item["id"]) == chat_id]
    if chat:
        lowered = chat.casefold()
        return [item for item in chats if lowered in str(item["display_name"]).casefold()]
    return chats


def fetch_messages(binary: str, chat_id: int, since: str, limit: int) -> list[dict[str, Any]]:
    return run_json(
        [
            binary,
            "messages",
            "--chat-id",
            str(chat_id),
            "--since",
            since,
            "--limit",
            str(limit),
            "--json",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill recent KakaoTalk messages into the live RAG store.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--binary",
        default=str(Path(__file__).resolve().parents[2] / ".build" / "release" / "kakaocli"),
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--since", default="7d")
    parser.add_argument("--chat")
    parser.add_argument("--chat-id", type=int)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--limit-chats", type=int, default=50)
    args = parser.parse_args()

    targets = resolve_targets(
        binary=args.binary,
        chat=args.chat,
        chat_id=args.chat_id,
        limit_chats=args.limit_chats,
    )
    if not targets:
        raise SystemExit("No target chats found for backfill.")

    store = LiveRAGStore(Path(args.db_path))
    store.upsert_chat_metadata(targets)

    total_messages = 0
    total_inserted = 0
    for target in targets:
        messages = fetch_messages(
            args.binary,
            int(target["id"]),
            args.since,
            args.limit,
        )
        normalized = [
            normalize_message(message, str(target.get("display_name") or ""))
            for message in messages
        ]
        if not normalized:
            continue
        result = post_json(f"{args.base_url.rstrip('/')}/kakao", normalized)
        total_messages += result["accepted"]
        total_inserted += result["inserted"]
        print(
            json.dumps(
                {
                    "chat_id": target["id"],
                    "chat_name": target.get("display_name"),
                    "accepted": result["accepted"],
                    "inserted": result["inserted"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    print(
        json.dumps(
            {
                "status": "done",
                "accepted": total_messages,
                "inserted": total_inserted,
                "chat_count": len(targets),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        sys.stderr.write(error.stderr or str(error))
        raise
    except urllib.error.URLError as error:
        raise SystemExit(f"Failed to reach webhook server: {error}") from error
