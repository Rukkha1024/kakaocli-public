"""Load and apply semantic chat eligibility policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = REPO_ROOT / "configs" / "live_rag_semantic_policy.yaml"
DEFAULT_POLICY_VERSION = "v1"


@dataclass(frozen=True)
class SemanticPolicy:
    """Semantic indexing policy for chat coverage."""

    default_max_member_count: int
    allow_chat_ids: tuple[int, ...]
    deny_chat_ids: tuple[int, ...]
    chat_overrides: dict[int, bool]
    signature: str
    source_path: Path
    version: str = DEFAULT_POLICY_VERSION

    def is_chat_eligible(self, *, chat_id: int, member_count: int) -> bool:
        if chat_id in self.deny_chat_ids:
            return False
        override = self.chat_overrides.get(chat_id)
        if override is not None:
            return bool(override)
        if chat_id in self.allow_chat_ids:
            return True
        return member_count <= self.default_max_member_count

    def as_config_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "default_max_member_count": self.default_max_member_count,
            "allow_chat_ids": list(self.allow_chat_ids),
            "deny_chat_ids": list(self.deny_chat_ids),
            "chat_overrides": {str(chat_id): enabled for chat_id, enabled in sorted(self.chat_overrides.items())},
        }


def load_semantic_policy(path: Path | None = None) -> SemanticPolicy:
    policy_path = path or DEFAULT_POLICY_PATH
    raw_payload = _read_policy_payload(policy_path)
    payload = _normalize_payload(raw_payload)
    signature = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return SemanticPolicy(
        default_max_member_count=int(payload["default_max_member_count"]),
        allow_chat_ids=tuple(int(chat_id) for chat_id in payload["allow_chat_ids"]),
        deny_chat_ids=tuple(int(chat_id) for chat_id in payload["deny_chat_ids"]),
        chat_overrides={int(chat_id): bool(enabled) for chat_id, enabled in payload["chat_overrides"].items()},
        signature=signature,
        source_path=policy_path,
        version=str(payload["version"]),
    )


def _read_policy_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"Semantic policy must be a mapping: {path}")
    return raw


def _normalize_payload(raw_payload: dict[str, Any]) -> dict[str, Any]:
    overrides_raw = raw_payload.get("chat_overrides") or {}
    if not isinstance(overrides_raw, dict):
        raise ValueError("`chat_overrides` must be a mapping of chat_id -> enabled.")

    overrides: dict[str, bool] = {}
    for raw_chat_id, raw_value in overrides_raw.items():
        enabled = raw_value
        if isinstance(raw_value, dict):
            enabled = raw_value.get("enabled")
        if enabled is None:
            raise ValueError(f"Missing enabled value for chat override {raw_chat_id}.")
        overrides[str(int(raw_chat_id))] = bool(enabled)

    return {
        "version": str(raw_payload.get("version") or DEFAULT_POLICY_VERSION),
        "default_max_member_count": int(raw_payload.get("default_max_member_count", 30)),
        "allow_chat_ids": sorted({int(chat_id) for chat_id in raw_payload.get("allow_chat_ids") or []}),
        "deny_chat_ids": sorted({int(chat_id) for chat_id in raw_payload.get("deny_chat_ids") or []}),
        "chat_overrides": overrides,
    }
