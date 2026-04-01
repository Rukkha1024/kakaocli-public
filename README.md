# kakaocli

CLI toolkit for KakaoTalk on macOS.
macOS용 카카오톡 CLI 툴킷.

> [!NOTE]
> This project does not call Kakao APIs or reimplement the KakaoTalk protocol. It works against data already stored on your Mac.

## What It Does

kakaocli lets you read, search, and send KakaoTalk messages from your terminal. It works entirely on your Mac using data that KakaoTalk already stores locally.

Two main workflows:

- **Core CLI** -- inspect chats, search messages, query the local database, and automate send/login flows.
- **Semantic Search (RAG)** -- build a local retrieval store and search your messages by meaning, not just exact keywords.

Three entrypoints:

| Script | Purpose |
|--------|---------|
| `./bin/install-kakaocli` | Install dependencies and build |
| `./bin/kakaocli-local` | The main CLI (13 commands) |
| `./bin/query-kakao` | Shortcut for RAG queries |

## Prerequisites

- macOS (Sonoma or later recommended)
- KakaoTalk.app installed and signed in at least once
- [Homebrew](https://brew.sh)

**macOS permissions** (grant to your terminal app):

| Permission | Where to enable | Required for |
|------------|----------------|--------------|
| Full Disk Access | System Settings > Privacy & Security > Full Disk Access | All commands (reads the encrypted local database) |
| Accessibility | System Settings > Privacy & Security > Accessibility | `send`, `harvest`, `inspect` (UI automation) |

## Installation

```bash
./bin/install-kakaocli
```

This script:

- Installs `sqlcipher` via Homebrew
- Builds the Swift binary (`.build/release/kakaocli`)
- Creates a repo-local Python `.venv` for the RAG pipeline
- Runs a small verification pass (skip with `--build-only`)

## Getting Started

Verify that everything works:

```bash
./bin/kakaocli-local status
./bin/kakaocli-local auth
./bin/kakaocli-local chats --limit 10
```

If `auth` succeeds, read-only commands are ready. Try a few:

```bash
./bin/kakaocli-local search "점심"
./bin/kakaocli-local messages --chat "지수" --since 7d
./bin/kakaocli-local send --me _ "test message"
```

Use `--me` to send messages to yourself when testing.

## Commands

### Reading Your Chats

| Command | Description |
|---------|-------------|
| `status` | Check app installation and local permissions |
| `auth` | Verify database decryption |
| `chats` | List chats by recent activity |
| `messages --chat "name"` | Read messages from a chat |
| `search "keyword"` | Search across all messages |
| `schema` | Dump the raw database schema |
| `query "SQL"` | Run read-only SQL against the local database |

All read commands support `--json` for structured output.

### Login & Credentials

`kakaocli login` saves credentials to macOS Keychain and launches KakaoTalk to log in automatically.

```bash
# Interactive prompt -- saves credentials, launches app, logs in
./bin/kakaocli-local login

# Provide credentials directly
./bin/kakaocli-local login --email user@example.com --password mypass

# Save credentials only (no login attempt)
./bin/kakaocli-local login --save-only

# Check login status
./bin/kakaocli-local login --status

# Remove saved credentials
./bin/kakaocli-local login --clear

```

To pre-set credentials for scripts, create a `.env` file at the repo root:

```dotenv
ID=your-email@example.com
PASSWORD=your-password
```

| Scenario | Behavior |
|----------|----------|
| `login` (no saved credentials) | Prompt > save to Keychain > launch app > log in |
| `login` (credentials exist) | Log in immediately with saved credentials |
| `login --email x --password y` | Save to Keychain > log in |
| `login --save-only` | Prompt > save to Keychain only |
| `login --status` | Show credential and app status |
| `login --clear` | Remove credentials from Keychain |
| Already logged in | Print "Already logged in" and exit |

### Sending & Automation

#### send

Send a message via UI automation.

```bash
./bin/kakaocli-local send "지수" "hello!"
./bin/kakaocli-local send --me _ "test message"
./bin/kakaocli-local send --dry-run "지수" "hello!"
```

| Option | Description |
|--------|-------------|
| `--me` | Send to yourself (for testing) |
| `--dry-run` | Show what would happen without sending |

#### sync

Watch for new messages in real time. Outputs NDJSON.

```bash
# Show current high-water mark (one-shot)
./bin/kakaocli-local sync

# Watch for new messages (Ctrl-C to stop)
./bin/kakaocli-local sync --follow

# Forward new messages to a webhook
./bin/kakaocli-local sync --follow --webhook https://example.com/hook

# Custom polling interval and start point
./bin/kakaocli-local sync --follow --interval 5 --since-log-id 123456
```

| Option | Default | Description |
|--------|---------|-------------|
| `--follow` | off | Continuously watch for new messages |
| `--webhook URL` | -- | POST new messages to this URL |
| `--interval N` | 2 | Polling interval in seconds |
| `--since-log-id N` | latest | Start from this log ID |

#### harvest

Capture chat display names from the KakaoTalk UI and save them to `~/.kakaocli/metadata.json`. Optionally open chats to load older message history.

```bash
# Capture chat names
./bin/kakaocli-local harvest

# Process only the 20 most recent chats
./bin/kakaocli-local harvest --top 20

# Open chats and load older messages
./bin/kakaocli-local harvest --scroll --max-clicks 15

# Preview without running
./bin/kakaocli-local harvest --dry-run
```

| Option | Default | Description |
|--------|---------|-------------|
| `--top N` | all | Process only the N most recent chats |
| `--scroll` | off | Open chats and click "load older messages" |
| `--max-clicks N` | 10 | Max "load older" clicks per chat |
| `--scroll-delay N` | 1.5 | Delay between actions in seconds |
| `--dry-run` | off | Show what would be processed without running |

#### inspect

Dump the KakaoTalk UI element tree. Useful for debugging automation commands.

```bash
# Full UI tree
./bin/kakaocli-local inspect

# Limit tree depth
./bin/kakaocli-local inspect --depth 3

# Open a specific chat and dump its window
./bin/kakaocli-local inspect --open-chat "지수"
```

| Option | Default | Description |
|--------|---------|-------------|
| `--depth N` | 5 | Maximum tree traversal depth |
| `--open-chat "name"` | -- | Open the named chat and dump that window |

## Semantic Search (RAG)

The RAG pipeline lets you search messages by meaning using embedding-based similarity, not just keyword matching. Data is stored locally at `.data/live_rag.sqlite3`.

### Setting up

A [HuggingFace token](https://huggingface.co/settings/tokens) is required for generating embeddings. Add it to your `.env` file:

```dotenv
HF_TOKEN=hf_your_token_here
```

Then initialize the RAG store (first-time only):

```bash
./bin/kakaocli-local rag init
```

This ingests your full message history and builds the semantic index. Use `--no-semantic` to skip embedding if you only need keyword search.

### Querying

```bash
# Search by meaning (default: hybrid mode)
./bin/kakaocli-local rag query "회의가 연기된 내용"

# Keyword-only search
./bin/kakaocli-local rag query --mode lexical "업데이트"

# JSON output
./bin/kakaocli-local rag query --mode semantic --json "일정 변경"
```

Or use the dedicated query shortcut with more options:

```bash
./bin/query-kakao --query-text "박다훈 업데이트" --json
./bin/query-kakao --mode hybrid --query-text "이번 주 회의 일정"
./bin/query-kakao --chat-id 123 --speaker "지수" --since-days 7 --query-text "점심"
```

### Keeping data current

```bash
# Incremental sync -- picks up new messages since last run
./bin/kakaocli-local rag update

# Check store and service status
./bin/kakaocli-local rag status
```

### Retrieval modes

| Mode | How it works |
|------|-------------|
| `lexical` | Exact and keyword-oriented matching (FTS5) |
| `semantic` | Embedding-based similarity search |
| `hybrid` | Merged lexical + semantic ranking (default) |

When semantic data is unavailable, `hybrid` falls back to `lexical` automatically and reports the fallback in JSON output.

The background retrieval service is managed automatically -- it starts on demand when you run a query. The indexing policy (which chats to include) is configured in `configs/live_rag_semantic_policy.yaml`.

## AI Integration

The CLI and RAG query path both emit structured JSON, so local scripts, editors, or custom agents can consume them directly.

```bash
./bin/kakaocli-local chats --json
./bin/kakaocli-local messages --chat "name" --since 1d --json
./bin/query-kakao --json --query-text "이번 주 회의 일정"
```

Use explicit user confirmation before sending messages to other people.

## Limitations

- macOS only
- KakaoTalk Mac sync history is incomplete until a chat has been opened on that Mac
- Group chat display names may remain `(unknown)` until `harvest` captures them from the UI
- Media and non-text messages are not fully rendered yet
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
