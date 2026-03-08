"""Minimal .env loader for local Live RAG tooling."""

from __future__ import annotations

import os
import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ENV_PATH = REPO_ROOT / ".env"
_ENV_ASSIGNMENT_PATTERN = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def load_repo_env(env_path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load repository .env entries into os.environ."""

    path = env_path or DEFAULT_ENV_PATH
    if not path.exists():
        return {}

    loaded: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_ASSIGNMENT_PATTERN.match(stripped)
        if not match:
            continue
        key, raw_value = match.groups()
        value = _parse_env_value(raw_value)
        if override or key not in os.environ:
            os.environ[key] = value
        loaded[key] = os.environ[key]
    return loaded


def _parse_env_value(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] in {"'", '"'} and value[-1:] == value[0]:
        return value[1:-1]
    comment_index = value.find(" #")
    if comment_index >= 0:
        value = value[:comment_index]
    return value.strip()
