"""Manage the launchd-backed Kakao Live RAG background service.

Writes the LaunchAgent plist, loads it, and checks
service health for local retrieval requests.
"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from .env_loader import load_repo_env
except ImportError:
    from env_loader import load_repo_env


load_repo_env()

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_URL = os.environ.get("LIVE_RAG_BASE_URL", "http://127.0.0.1:8765")
DEFAULT_DB_PATH = Path(os.environ.get("LIVE_RAG_DB_PATH", REPO_ROOT / ".data" / "live_rag.sqlite3"))
DEFAULT_BINARY = REPO_ROOT / ".build" / "release" / "kakaocli"
DEFAULT_INTERVAL = 2.0
DEFAULT_LABEL = "io.rukkha.kakaocli-live-rag"


def healthcheck(base_url: str = DEFAULT_BASE_URL, timeout: float = 2.0) -> dict[str, Any] | None:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (TimeoutError, urllib.error.URLError, json.JSONDecodeError):
        return None


def wait_for_health(base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        payload = healthcheck(base_url=base_url, timeout=2.0)
        if payload is not None and payload.get("status") == "ok":
            return payload
        time.sleep(1.0)
    raise TimeoutError(f"Live RAG service did not become healthy within {timeout:.1f}s.")


def launch_agent_label() -> str:
    return os.environ.get("LIVE_RAG_LAUNCHD_LABEL", DEFAULT_LABEL)


def launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{launch_agent_label()}.plist"


def launch_agent_target() -> str:
    return f"gui/{os.getuid()}/{launch_agent_label()}"


def launch_agent_domain() -> str:
    return f"gui/{os.getuid()}"


def log_dir() -> Path:
    path = REPO_ROOT / ".data" / "live_rag_logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_python_executable() -> str:
    candidates = [
        os.environ.get("LIVE_RAG_PYTHON"),
        sys.executable,
        shutil.which("python3"),
    ]

    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate).expanduser()
        if candidate_path.exists():
            return str(candidate_path)

    raise RuntimeError("Could not resolve the absolute path to Python.")


def parse_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlsplit(base_url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port is not None:
        return host, parsed.port
    return host, 443 if parsed.scheme == "https" else 80


def build_launch_agent(
    *,
    base_url: str = DEFAULT_BASE_URL,
    db_path: Path = DEFAULT_DB_PATH,
    binary: Path = DEFAULT_BINARY,
    interval: float = DEFAULT_INTERVAL,
    service_mode: str = "follow",
) -> bytes:
    host, port = parse_host_port(base_url)
    stdout_path = log_dir() / "launchd.stdout.log"
    stderr_path = log_dir() / "launchd.stderr.log"
    python_exe = resolve_python_executable()
    plist = {
        "Label": launch_agent_label(),
        "ProgramArguments": [
            python_exe,
            str(REPO_ROOT / "tools" / "live_rag" / "supervisor.py"),
            "--base-url",
            base_url,
            "--db-path",
            str(db_path),
            "--binary",
            str(binary),
            "--interval",
            str(interval),
            "--service-mode",
            service_mode,
        ],
        "WorkingDirectory": str(REPO_ROOT),
        "EnvironmentVariables": {
            "PYTHONUNBUFFERED": "1",
            "LIVE_RAG_BASE_URL": base_url,
            "LIVE_RAG_DB_PATH": str(db_path),
            "LIVE_RAG_PYTHON": python_exe,
            "LIVE_RAG_SERVICE_MODE": service_mode,
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    return plistlib.dumps(plist, sort_keys=False)


def run_launchctl(arguments: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["launchctl", *arguments],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or f"launchctl {' '.join(arguments)} failed"
        raise RuntimeError(message)
    return result


def is_agent_loaded() -> bool:
    return run_launchctl(["print", launch_agent_target()], check=False).returncode == 0


def write_launch_agent(
    *,
    base_url: str = DEFAULT_BASE_URL,
    db_path: Path = DEFAULT_DB_PATH,
    binary: Path = DEFAULT_BINARY,
    interval: float = DEFAULT_INTERVAL,
    service_mode: str = "follow",
) -> dict[str, Any]:
    path = launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = build_launch_agent(
        base_url=base_url,
        db_path=db_path,
        binary=binary,
        interval=interval,
        service_mode=service_mode,
    )
    previous = path.read_bytes() if path.exists() else None
    changed = previous != content
    if changed:
        path.write_bytes(content)
    return {"path": str(path), "changed": changed}


def bootstrap_agent() -> None:
    run_launchctl(["bootstrap", launch_agent_domain(), str(launch_agent_path())], check=True)


def bootout_agent() -> None:
    run_launchctl(["bootout", launch_agent_target()], check=False)


def kickstart_agent() -> None:
    run_launchctl(["kickstart", "-k", launch_agent_target()], check=True)


def install_launch_agent(
    *,
    base_url: str = DEFAULT_BASE_URL,
    db_path: Path = DEFAULT_DB_PATH,
    binary: Path = DEFAULT_BINARY,
    interval: float = DEFAULT_INTERVAL,
    service_mode: str = "follow",
) -> dict[str, Any]:
    write_result = write_launch_agent(
        base_url=base_url,
        db_path=db_path,
        binary=binary,
        interval=interval,
        service_mode=service_mode,
    )
    loaded = is_agent_loaded()
    if write_result["changed"] and loaded:
        bootout_agent()
        loaded = False
    if not loaded:
        bootstrap_agent()
    return {
        "label": launch_agent_label(),
        "path": write_result["path"],
        "changed": write_result["changed"],
        "loaded": True,
    }


def uninstall_launch_agent(remove_file: bool = True) -> dict[str, Any]:
    loaded = is_agent_loaded()
    if loaded:
        bootout_agent()
    path = launch_agent_path()
    removed = False
    if remove_file and path.exists():
        path.unlink()
        removed = True
    return {"label": launch_agent_label(), "path": str(path), "loaded": False, "removed": removed}


def status(base_url: str = DEFAULT_BASE_URL, service_mode: str = "follow") -> dict[str, Any]:
    path = launch_agent_path()
    return {
        "label": launch_agent_label(),
        "launch_agent_path": str(path),
        "launch_agent_exists": path.exists(),
        "loaded": is_agent_loaded(),
        "health": healthcheck(base_url=base_url),
        "log_dir": str(log_dir()),
        "service_mode": service_mode,
    }


def ensure_running(
    *,
    base_url: str = DEFAULT_BASE_URL,
    db_path: Path = DEFAULT_DB_PATH,
    binary: Path = DEFAULT_BINARY,
    interval: float = DEFAULT_INTERVAL,
    timeout: float = 30.0,
    service_mode: str = "follow",
) -> dict[str, Any]:
    current = healthcheck(base_url=base_url)
    if current is not None and current.get("status") == "ok":
        install_result = write_launch_agent(
            base_url=base_url,
            db_path=db_path,
            binary=binary,
            interval=interval,
            service_mode=service_mode,
        )
        return {
            "status": "ok",
            "source": "already-running",
            "install": install_result,
            "loaded": is_agent_loaded(),
            "health": current,
            "service_mode": service_mode,
        }

    install_result = install_launch_agent(
        base_url=base_url,
        db_path=db_path,
        binary=binary,
        interval=interval,
        service_mode=service_mode,
    )
    kickstart_agent()
    healthy = wait_for_health(base_url=base_url, timeout=timeout)
    return {
        "status": "ok",
        "source": "launchd",
        "install": install_result,
        "health": healthy,
        "service_mode": service_mode,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the local launchd service for Kakao Live RAG.")
    parser.add_argument("command", choices=["install", "ensure", "status", "start", "stop", "uninstall"])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--binary", default=str(DEFAULT_BINARY))
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--service-mode", choices=["follow", "server-only", "off"], default="follow")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    binary = Path(args.binary)

    if args.command == "install":
        result = install_launch_agent(
            base_url=args.base_url,
            db_path=db_path,
            binary=binary,
            interval=args.interval,
            service_mode=args.service_mode,
        )
    elif args.command == "ensure":
        result = ensure_running(
            base_url=args.base_url,
            db_path=db_path,
            binary=binary,
            interval=args.interval,
            timeout=args.timeout,
            service_mode=args.service_mode,
        )
    elif args.command == "status":
        result = status(base_url=args.base_url, service_mode=args.service_mode)
    elif args.command == "start":
        install_launch_agent(
            base_url=args.base_url,
            db_path=db_path,
            binary=binary,
            interval=args.interval,
            service_mode=args.service_mode,
        )
        kickstart_agent()
        result = status(base_url=args.base_url, service_mode=args.service_mode)
    elif args.command == "stop":
        bootout_agent()
        result = status(base_url=args.base_url, service_mode=args.service_mode)
    else:
        result = uninstall_launch_agent(remove_file=True)

    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
