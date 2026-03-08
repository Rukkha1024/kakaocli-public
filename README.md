# kakaocli

CLI toolkit for KakaoTalk on macOS. It reads the local SQLCipher database in read-only mode, automates the native app when needed, and includes a local Live RAG stack for evidence-backed retrieval.

macOS용 카카오톡 CLI 툴킷입니다. 로컬 SQLCipher DB를 읽기 전용으로 다루고, 필요할 때 네이티브 앱을 자동화하며, 근거 기반 검색을 위한 로컬 Live RAG 도구를 함께 제공합니다.

<p align="center">
  <img src="assets/demo.svg" alt="kakaocli demo" width="820">
</p>

> [!NOTE]
> This project does not call Kakao APIs or reimplement the KakaoTalk protocol. It works against data already stored on your Mac.

## Overview

`kakaocli` covers two workflows:

- Core CLI: inspect chats, search messages, query the local database, and automate send/login/harvest flows.
- Live RAG: keep a local retrieval store and query it through `./bin/query-kakao`.

The repo root is the product root. The main entrypoints are:

- `./bin/install-kakaocli`
- `./bin/kakaocli-local`
- `./bin/query-kakao`

## Quick Start

### 1. Install dependencies and build

```bash
./bin/install-kakaocli
```

This script:

- ensures `sqlcipher` is installed with Homebrew
- builds `.build/release/kakaocli`
- prepares a repo-local `.venv` for the Live RAG helpers
- runs a small local verification pass unless `--build-only` is used

### 2. Grant macOS permissions

Grant these permissions to the terminal app you use:

- Full Disk Access: required to read KakaoTalk's encrypted local database
- Accessibility: required for UI automation commands such as `send`, `harvest`, and `inspect`

### 3. Verify local access

```bash
./bin/kakaocli-local status
./bin/kakaocli-local auth
./bin/kakaocli-local chats --limit 10
```

If `auth` succeeds, read-only commands are ready to use.

### 4. Run common commands

```bash
./bin/kakaocli-local search "점심"
./bin/kakaocli-local messages --chat "지수" --since 7d
./bin/kakaocli-local send --me _ "test message"
./bin/kakaocli-local sync --follow
./bin/kakaocli-local query "SELECT COUNT(*) FROM NTChatMessage"
```

Use `--me` when testing send flows.

## Core CLI

### Read commands

| Command | Description |
|---------|-------------|
| `./bin/kakaocli-local status` | Check app installation and local permissions |
| `./bin/kakaocli-local auth` | Verify database decryption |
| `./bin/kakaocli-local chats` | List chats by recent activity |
| `./bin/kakaocli-local messages --chat "name"` | Read messages from a chat |
| `./bin/kakaocli-local search "keyword"` | Search across all messages |
| `./bin/kakaocli-local schema` | Dump the raw DB schema |
| `./bin/kakaocli-local query "SQL"` | Run read-only SQL against the local DB |

All read commands support `--json`.

### Write and automation commands

```bash
./bin/kakaocli-local send "chat name" "message"
./bin/kakaocli-local send --me _ "message"
./bin/kakaocli-local harvest
./bin/kakaocli-local login --status
```

`send`, `sync`, and `harvest` will launch KakaoTalk and use stored credentials when required.

## Live RAG

The local retrieval store lives at `.data/live_rag.sqlite3`. It keeps canonical message rows plus semantic sidecar data used by the retrieval service.

### Query the store

```bash
./bin/query-kakao --json --query-text "박다훈 업데이트"
./bin/query-kakao --json --mode lexical --query-text "업데이트"
./bin/query-kakao --json --mode semantic --query-text "회의가 연기된 내용"
./bin/query-kakao --json --mode hybrid --query-text "박다훈이 미룬 일정"
```

Supported retrieval modes:

- `lexical`: exact and keyword-oriented matching
- `semantic`: embedding-based similarity search
- `hybrid`: merged lexical + semantic ranking

When semantic retrieval is unavailable, `hybrid` falls back to lexical and reports the fallback in JSON.

### Build, update, and validate semantic data

Authenticate with Hugging Face first if you plan to build embeddings:

```bash
export HF_TOKEN=hf_xxx
```

Then run:

```bash
conda run -n module python tools/live_rag/build_semantic_index.py --mode update
HF_TOKEN=hf_xxx conda run -n module python tools/live_rag/build_semantic_index.py --mode rebuild --batch-size 20 --progress
HF_TOKEN=hf_xxx conda run -n module python tools/live_rag/validate_semantic.py --use-temp-db
```

The current policy indexes normal text messages from chats whose `member_count` is `<= 30`, unless explicitly overridden in `configs/live_rag_semantic_policy.yaml`.

### Background service

`./bin/query-kakao` uses the launchd-backed Live RAG service. Service management commands are available through:

```bash
conda run -n module python tools/live_rag/service_manager.py status
conda run -n module python tools/live_rag/service_manager.py ensure
conda run -n module python tools/live_rag/service_manager.py start
conda run -n module python tools/live_rag/service_manager.py stop
```

The default launchd label is `io.rukkha.kakaocli-live-rag`.

## AI Integration

The CLI and Live RAG query path both emit structured JSON, so local scripts, editors, or custom agents can consume them directly.

Examples:

```bash
./bin/kakaocli-local chats --json
./bin/kakaocli-local messages --chat "name" --since 1d --json
./bin/query-kakao --json --query-text "이번 주 회의 일정"
```

Use explicit user confirmation before sending messages to other people.

## Limitations

- macOS only
- KakaoTalk Mac sync history is incomplete until a chat has been opened on that Mac
- group chat display names may remain `(unknown)` until `harvest` captures them from the UI
- media and non-text messages are not fully rendered yet
- KakaoTalk allows one logged-in Mac per account

## Disclaimer

> This project is not affiliated with Kakao Corp.
>
> It reads KakaoTalk data already stored on your Mac and automates the native app through standard macOS Accessibility APIs.
>
> It does not call Kakao APIs, reimplement the KakaoTalk protocol, or modify the KakaoTalk application.

## Credits

Developed by **[Brian ByungHyun Shin](https://github.com/brianshin22)** at **[Silver Flight Group](https://github.com/silver-flight-group)**.

Database decryption approach based on research by [blluv](https://gist.github.com/blluv/8418e3ef4f4aa86004657ea524f2de14).

Inspired by [wacli](https://github.com/steipete/wacli) by Peter Steinberger.
