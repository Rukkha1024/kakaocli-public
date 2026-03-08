"""Supervise the local webhook server and kakaocli sync follower.

Runs both child processes together so launchd can keep
the Live RAG stack alive across login sessions.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

try:
    from .service_manager import DEFAULT_BASE_URL, DEFAULT_BINARY, DEFAULT_DB_PATH, healthcheck, parse_host_port
except ImportError:
    from service_manager import DEFAULT_BASE_URL, DEFAULT_BINARY, DEFAULT_DB_PATH, healthcheck, parse_host_port


REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PATH = REPO_ROOT / "tools" / "live_rag" / "app.py"
RUN_SYNC_PATH = REPO_ROOT / "tools" / "live_rag" / "run_sync.py"


class LiveRAGSupervisor:
    def __init__(self, *, base_url: str, db_path: Path, binary: Path, interval: float, service_mode: str) -> None:
        self.base_url = base_url
        self.db_path = db_path
        self.binary = binary
        self.interval = interval
        self.service_mode = service_mode
        self.host, self.port = parse_host_port(base_url)
        self.processes: list[subprocess.Popen[str]] = []
        self.stopping = False

    def run(self) -> int:
        self._register_signals()
        app_process = self._spawn_app()
        self.processes.append(app_process)
        self._wait_for_app_health(app_process)

        sync_process: subprocess.Popen[str] | None = None
        if self.service_mode == "follow":
            sync_process = self._spawn_sync()
            self.processes.append(sync_process)

        while not self.stopping:
            app_exit = app_process.poll()
            if app_exit is not None:
                if sync_process is not None:
                    self._terminate_process(sync_process, "sync")
                return app_exit or 1

            sync_exit = sync_process.poll() if sync_process is not None else None
            if sync_process is not None and sync_exit is not None:
                if self.stopping:
                    break
                time.sleep(3.0)
                sync_process = self._spawn_sync()
                self.processes[-1] = sync_process

            time.sleep(1.0)

        self._shutdown()
        return 0

    def _register_signals(self) -> None:
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    def _handle_signal(self, signum: int, _frame: object) -> None:
        self.stopping = True
        print(f"live-rag supervisor received signal {signum}", flush=True)

    def _spawn_app(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["LIVE_RAG_DB_PATH"] = str(self.db_path)
        print(f"starting live-rag app on {self.host}:{self.port}", flush=True)
        return subprocess.Popen(
            [
                sys.executable,
                str(APP_PATH),
                "--host",
                self.host,
                "--port",
                str(self.port),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
        )

    def _spawn_sync(self) -> subprocess.Popen[str]:
        env = os.environ.copy()
        env["LIVE_RAG_DB_PATH"] = str(self.db_path)
        print("starting kakaocli sync follower", flush=True)
        return subprocess.Popen(
            [
                sys.executable,
                str(RUN_SYNC_PATH),
                "--base-url",
                self.base_url,
                "--db-path",
                str(self.db_path),
                "--binary",
                str(self.binary),
                "--interval",
                str(self.interval),
            ],
            cwd=REPO_ROOT,
            env=env,
            text=True,
        )

    def _wait_for_app_health(self, app_process: subprocess.Popen[str], timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app_exit = app_process.poll()
            if app_exit is not None:
                raise SystemExit(f"live-rag app exited before becoming healthy: {app_exit}")
            if healthcheck(base_url=self.base_url, timeout=2.0):
                print("live-rag app is healthy", flush=True)
                return
            time.sleep(1.0)
        raise SystemExit(f"live-rag app did not become healthy within {timeout:.1f}s")

    def _shutdown(self) -> None:
        for process, name in zip(reversed(self.processes), ["sync", "app"], strict=False):
            self._terminate_process(process, name)

    def _terminate_process(self, process: subprocess.Popen[str], name: str) -> None:
        if process.poll() is not None:
            return
        print(f"stopping {name}", flush=True)
        process.terminate()
        try:
            process.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run and supervise the local Kakao Live RAG services.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--binary", default=str(DEFAULT_BINARY))
    parser.add_argument("--interval", type=float, default=2.0)
    parser.add_argument("--service-mode", choices=("follow", "server-only", "off"), default="follow")
    args = parser.parse_args()

    supervisor = LiveRAGSupervisor(
        base_url=args.base_url,
        db_path=Path(args.db_path),
        binary=Path(args.binary),
        interval=args.interval,
        service_mode=args.service_mode,
    )
    raise SystemExit(supervisor.run())


if __name__ == "__main__":
    main()
