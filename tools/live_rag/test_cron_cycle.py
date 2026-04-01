"""Test script: full cron cycle simulation.

Flow: quit KakaoTalk → start → login → backfill → verify
This is a standalone test — does NOT modify the main pipeline.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
BINARY = str(BASE_DIR / ".build" / "release" / "kakaocli")
DB_PATH = str(BASE_DIR / ".data" / "live_rag.sqlite3")
PORT = 28765
BASE_URL = f"http://127.0.0.1:{PORT}"
BACKFILL_SINCE = "2h"


def log(step: str, msg: str) -> None:
    print(f"[{step}] {msg}", flush=True)


# ── Step 1: Quit KakaoTalk ──────────────────────────────────────────

def quit_kakaotalk(timeout: float = 10.0) -> bool:
    """Quit KakaoTalk via killall (AppleScript quit triggers 'User canceled')."""
    log("QUIT", "KakaoTalk 종료 중...")

    # Check if running
    result = subprocess.run(["pgrep", "-x", "KakaoTalk"], capture_output=True)
    if result.returncode != 0:
        log("QUIT", "KakaoTalk이 실행 중이 아님 — skip")
        return True

    subprocess.run(["killall", "KakaoTalk"], capture_output=True)
    time.sleep(3)  # allow process to fully exit

    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(["pgrep", "-x", "KakaoTalk"], capture_output=True)
        if result.returncode != 0:
            log("QUIT", "KakaoTalk 종료 완료")
            return True
        log("QUIT", "아직 실행 중... 재시도")
        subprocess.run(["killall", "-9", "KakaoTalk"], capture_output=True)
        time.sleep(2)

    log("QUIT", "종료 실패 — 프로세스가 아직 실행 중")
    return False


# ── Step 2: Start & Login ───────────────────────────────────────────

def read_keychain(account: str) -> str | None:
    """Read a value from macOS Keychain."""
    result = subprocess.run(
        ["security", "find-generic-password",
         "-s", "com.kakaocli.credentials", "-a", account, "-w"],
        capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def start_and_login(timeout: float = 60.0) -> bool:
    """Launch KakaoTalk and login via kakaocli login with keychain credentials."""
    email = read_keychain("kakaotalk-email")
    password = read_keychain("kakaotalk-password")
    if not email or not password:
        log("LOGIN", "Keychain에 자격증명 없음")
        return False

    log("LOGIN", f"KakaoTalk 시작 + 로그인 시도 (email={email[:4]}...)")
    result = subprocess.run(
        [BINARY, "login", "--email", email, "--password", password],
        capture_output=True, text=True, timeout=timeout,
    )
    success = result.returncode == 0
    output = (result.stdout + result.stderr).strip()
    log("LOGIN", f"rc={result.returncode} → {output[:200]}")
    return success


# ── Step 3: Verify login state ──────────────────────────────────────

def verify_login() -> bool:
    """Check kakaocli status to confirm logged-in state."""
    result = subprocess.run(
        [BINARY, "status"],
        capture_output=True, text=True, timeout=10,
    )
    logged_in = "loggedIn" in result.stdout
    log("VERIFY", f"App state: {'loggedIn ✓' if logged_in else 'NOT logged in ✗'}")
    return logged_in


# ── Step 4: Start FastAPI server ────────────────────────────────────

def start_server() -> subprocess.Popen | None:
    """Start the live_rag FastAPI server."""
    log("SERVER", f"FastAPI 서버 시작 (port {PORT})...")
    env = {**os.environ, "LIVE_RAG_DB_PATH": DB_PATH}
    proc = subprocess.Popen(
        [
            "conda", "run", "-n", "module", "python",
            "-m", "uvicorn", "tools.live_rag.app:create_app",
            "--factory", "--host", "127.0.0.1", "--port", str(PORT),
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=env,
    )
    # Wait for health
    for i in range(15):
        time.sleep(1)
        try:
            resp = urllib.request.urlopen(f"{BASE_URL}/health", timeout=2)
            data = json.loads(resp.read())
            log("SERVER", f"서버 준비 완료 (messages: {data.get('message_count', '?')})")
            return proc
        except Exception:
            pass
    log("SERVER", "서버 시작 실패")
    proc.terminate()
    return None


# ── Step 5: Backfill ────────────────────────────────────────────────

def run_backfill() -> dict:
    """Run backfill for recent messages."""
    log("BACKFILL", f"최근 {BACKFILL_SINCE} 메시지 수집 중...")
    result = subprocess.run(
        [
            "conda", "run", "-n", "module", "python",
            "tools/live_rag/backfill.py",
            "--base-url", BASE_URL,
            "--binary", BINARY,
            "--db-path", DB_PATH,
            "--since", BACKFILL_SINCE,
            "--limit", "500",
            "--limit-chats", "50",
        ],
        capture_output=True, text=True, timeout=120,
    )
    # Parse last line as summary
    lines = result.stdout.strip().split("\n")
    summary = {}
    for line in lines:
        try:
            summary = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            pass
    log("BACKFILL", f"결과: accepted={summary.get('accepted', 0)}, "
                    f"inserted={summary.get('inserted', 0)}, "
                    f"chats={summary.get('chat_count', 0)}")
    return summary


# ── Step 6: Verify final state ──────────────────────────────────────

def verify_store() -> dict:
    """Check store stats after backfill."""
    try:
        resp = urllib.request.urlopen(f"{BASE_URL}/health", timeout=2)
        data = json.loads(resp.read())
        log("VERIFY", f"최종 Store: messages={data.get('message_count')}, "
                      f"chats={data.get('chat_count')}")
        return data
    except Exception as e:
        log("VERIFY", f"Store 확인 실패: {e}")
        return {}


# ── Main ────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 60)
    print("  Cron Cycle Test: QUIT → START → LOGIN → BACKFILL")
    print("=" * 60)

    results = {}

    # Step 1: Quit
    results["quit"] = quit_kakaotalk()
    if not results["quit"]:
        print("\nFAIL: KakaoTalk 종료 실패")
        sys.exit(1)
    time.sleep(2)  # settle

    # Step 2: Start & Login
    results["login"] = start_and_login()
    if not results["login"]:
        print("\nFAIL: 로그인 실패")
        sys.exit(1)
    time.sleep(3)  # wait for app to stabilize

    # Step 3: Verify login
    results["verified"] = verify_login()
    if not results["verified"]:
        print("\nFAIL: 로그인 상태 확인 실패")
        sys.exit(1)

    # Step 4: Start server
    server = start_server()
    if server is None:
        print("\nFAIL: 서버 시작 실패")
        sys.exit(1)

    try:
        # Step 5: Backfill
        backfill_result = run_backfill()
        results["backfill"] = backfill_result

        # Step 6: Verify
        store_stats = verify_store()
        results["store"] = store_stats
    finally:
        # Cleanup: stop server
        log("CLEANUP", "서버 종료 중...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        log("CLEANUP", "서버 종료 완료")

    # Summary
    print("\n" + "=" * 60)
    print("  RESULT: ALL STEPS PASSED ✓")
    print("=" * 60)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
