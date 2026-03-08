"""Run deterministic smoke and benchmark validation for Live RAG."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

try:
    from .embedding_client import ExternalEmbeddingClient
    from .eval_support import (
        DeterministicEmbeddingClient,
        FIXTURE_EMBEDDING_MODEL,
        FIXTURE_EMBEDDING_PROVIDER,
        REFERENCE_SNAPSHOT_PATH,
        build_reference_snapshot,
        evaluate_benchmark,
        load_benchmark_cases,
        md5_hex,
        seed_fixture_store,
    )
    from .policy import DEFAULT_POLICY_PATH, SemanticPolicy, load_semantic_policy
    from .semantic_index import DEFAULT_EMBEDDING_MODEL
except ImportError:
    from embedding_client import ExternalEmbeddingClient
    from eval_support import (
        DeterministicEmbeddingClient,
        FIXTURE_EMBEDDING_MODEL,
        FIXTURE_EMBEDDING_PROVIDER,
        REFERENCE_SNAPSHOT_PATH,
        build_reference_snapshot,
        evaluate_benchmark,
        load_benchmark_cases,
        md5_hex,
        seed_fixture_store,
    )
    from policy import DEFAULT_POLICY_PATH, SemanticPolicy, load_semantic_policy
    from semantic_index import DEFAULT_EMBEDDING_MODEL


SMOKE_QUERY = "회의가 연기됐다는 내용"
SMOKE_EXPECTED_LOG_ID = 101
FIXTURE_ALLOWLIST_CHAT_ID = 9900


def run_validation(
    *,
    db_path: Path,
    backend: str,
    validation_mode: str,
    embedding_model: str,
    embedding_provider: str | None,
    policy: SemanticPolicy,
    reference_snapshot_path: Path,
) -> dict[str, Any]:
    client = _build_client(
        backend=backend,
        embedding_model=embedding_model,
        embedding_provider=embedding_provider,
    )
    effective_policy = _fixture_policy(policy)
    store = seed_fixture_store(db_path=db_path, policy=effective_policy, embedding_client=client)
    benchmark_cases = load_benchmark_cases()

    payload: dict[str, Any] = {
        "status": "ok",
        "backend": backend,
        "embedding_model": getattr(client, "model", embedding_model),
        "embedding_provider": getattr(client, "provider", embedding_provider),
        "policy_signature": effective_policy.signature,
    }
    if validation_mode in {"smoke", "all"}:
        payload["smoke"] = run_smoke_validation(store=store, client=client)
    if validation_mode in {"benchmark", "all"}:
        payload["benchmark"] = evaluate_benchmark(store=store, client=client, cases=benchmark_cases)
    if validation_mode in {"snapshot", "all"}:
        snapshot = build_reference_snapshot(store=store, client=client, cases=benchmark_cases)
        snapshot_md5 = md5_hex(snapshot)
        snapshot_payload: dict[str, Any] = {"md5": snapshot_md5, "snapshot": snapshot}
        if reference_snapshot_path.exists():
            reference_snapshot = json.loads(reference_snapshot_path.read_text(encoding="utf-8"))
            snapshot_payload["reference_md5"] = md5_hex(reference_snapshot)
            snapshot_payload["matches_reference"] = reference_snapshot == snapshot
        payload["snapshot"] = snapshot_payload
    return payload


def run_smoke_validation(*, store: Any, client: Any) -> dict[str, Any]:
    settings = store.get_semantic_settings()
    if settings is None:
        raise RuntimeError("Semantic settings were not persisted.")
    hits = store.retrieve_semantic(
        query_vector=client.embed_query(SMOKE_QUERY),
        limit=3,
        semantic_top_k=3,
        config_signature=settings["config_signature"],
    )
    hit_log_ids = [int(hit["message"]["log_id"]) for hit in hits]
    if SMOKE_EXPECTED_LOG_ID not in hit_log_ids:
        raise RuntimeError(
            f"Expected fixture log_id {SMOKE_EXPECTED_LOG_ID} in semantic hits, got {hit_log_ids}"
        )
    return {
        "fixture_query": SMOKE_QUERY,
        "expected_log_id": SMOKE_EXPECTED_LOG_ID,
        "semantic_hit_log_ids": hit_log_ids,
    }


def _build_client(*, backend: str, embedding_model: str, embedding_provider: str | None) -> Any:
    if backend == "deterministic":
        return DeterministicEmbeddingClient()
    return ExternalEmbeddingClient(model=embedding_model, provider=embedding_provider)


def _fixture_policy(policy: SemanticPolicy) -> SemanticPolicy:
    allow_chat_ids = tuple(sorted(set(policy.allow_chat_ids) | {FIXTURE_ALLOWLIST_CHAT_ID}))
    signature = md5_hex(
        {
            "base_policy_signature": policy.signature,
            "fixture_allow_chat_ids": list(allow_chat_ids),
            "default_max_member_count": policy.default_max_member_count,
            "deny_chat_ids": list(policy.deny_chat_ids),
            "chat_overrides": policy.chat_overrides,
        }
    )
    return SemanticPolicy(
        default_max_member_count=policy.default_max_member_count,
        allow_chat_ids=allow_chat_ids,
        deny_chat_ids=policy.deny_chat_ids,
        chat_overrides=policy.chat_overrides,
        signature=signature,
        source_path=policy.source_path,
        version=policy.version,
    )


def _default_model_for_backend(backend: str) -> str:
    if backend == "deterministic":
        return FIXTURE_EMBEDDING_MODEL
    return DEFAULT_EMBEDDING_MODEL


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Kakao Live RAG retrieval.")
    parser.add_argument("--db-path")
    parser.add_argument("--use-temp-db", action="store_true")
    parser.add_argument("--backend", choices=("deterministic", "huggingface"), default="deterministic")
    parser.add_argument("--validation", choices=("smoke", "benchmark", "snapshot", "all"), default="all")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-provider")
    parser.add_argument("--policy-path", default=str(DEFAULT_POLICY_PATH))
    parser.add_argument("--reference-snapshot-path", default=str(REFERENCE_SNAPSHOT_PATH))
    parser.add_argument("--write-reference-snapshot", action="store_true")
    args = parser.parse_args()

    embedding_model = args.embedding_model or _default_model_for_backend(args.backend)
    policy = load_semantic_policy(Path(args.policy_path))
    reference_snapshot_path = Path(args.reference_snapshot_path)

    try:
        if args.use_temp_db:
            with tempfile.TemporaryDirectory(prefix="live-rag-semantic-") as temp_dir:
                payload = run_validation(
                    db_path=Path(temp_dir) / "fixture.sqlite3",
                    backend=args.backend,
                    validation_mode=args.validation,
                    embedding_model=embedding_model,
                    embedding_provider=args.embedding_provider,
                    policy=policy,
                    reference_snapshot_path=reference_snapshot_path,
                )
                if args.write_reference_snapshot and "snapshot" in payload:
                    reference_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
                    reference_snapshot_path.write_text(
                        json.dumps(payload["snapshot"]["snapshot"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            return

        if not args.db_path:
            raise SystemExit("Provide --db-path or use --use-temp-db.")

        payload = run_validation(
            db_path=Path(args.db_path),
            backend=args.backend,
            validation_mode=args.validation,
            embedding_model=embedding_model,
            embedding_provider=args.embedding_provider,
            policy=policy,
            reference_snapshot_path=reference_snapshot_path,
        )
        if args.write_reference_snapshot and "snapshot" in payload:
            reference_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            reference_snapshot_path.write_text(
                json.dumps(payload["snapshot"]["snapshot"], ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    except Exception as error:
        payload = {
            "status": "error",
            "stage": "validate_semantic",
            "message": str(error),
            "backend": args.backend,
            "model": embedding_model,
            "provider": args.embedding_provider,
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
