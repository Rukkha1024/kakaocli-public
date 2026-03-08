"""Regression tests for the Kakao Live RAG evidence layer."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]

import sys

sys.path.insert(0, str(REPO_ROOT))

from tools.live_rag.app import build_retrieval_response
from tools.live_rag.embedding_client import ExternalEmbeddingClient
from tools.live_rag.eval_support import (
    REFERENCE_SNAPSHOT_PATH,
    DeterministicEmbeddingClient,
    build_reference_snapshot,
    load_benchmark_cases,
    md5_hex,
    seed_fixture_store,
)
from tools.live_rag.policy import SemanticPolicy, load_semantic_policy


class LiveRAGRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base_policy = load_semantic_policy()

    def test_reference_snapshot_matches_committed_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = seed_fixture_store(
                db_path=Path(temp_dir) / "fixture.sqlite3",
                policy=self._fixture_policy(),
                embedding_client=DeterministicEmbeddingClient(),
            )
            snapshot = build_reference_snapshot(
                store=store,
                client=DeterministicEmbeddingClient(),
                cases=load_benchmark_cases(),
            )
        reference = json.loads(REFERENCE_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(snapshot, reference)
        self.assertEqual(md5_hex(snapshot), "fb7059a13b81bfe358c51657ba3aadf0")

    def test_retrieval_response_exposes_evidence_contract(self) -> None:
        class PatchedDeterministicEmbeddingClient(DeterministicEmbeddingClient):
            def __init__(self, *_: object, **__: object) -> None:
                super().__init__()

        with tempfile.TemporaryDirectory() as temp_dir:
            store = seed_fixture_store(
                db_path=Path(temp_dir) / "fixture.sqlite3",
                policy=self._fixture_policy(),
                embedding_client=DeterministicEmbeddingClient(),
            )
            with patch("tools.live_rag.app.ExternalEmbeddingClient", PatchedDeterministicEmbeddingClient):
                response = build_retrieval_response(
                    store,
                    {
                        "query": "박다훈 업데이트",
                        "mode": "hybrid",
                        "limit": 3,
                        "rerank": "auto",
                    },
                )
        self.assertEqual(response["requested_mode"], "hybrid")
        self.assertEqual(response["actual_mode"], "hybrid")
        self.assertEqual(response["embedding_model"], "fixture/deterministic-v1")
        self.assertIn("query_profile_version", response)
        self.assertTrue(response["hits"])
        top_hit = response["hits"][0]
        self.assertEqual(top_hit["message"]["log_id"], 104)
        self.assertIn("matched_chunk_id", top_hit)
        self.assertIn("matched_chunk_text", top_hit)
        self.assertIn("retrieval_sources", top_hit)
        self.assertIn("semantic", top_hit["retrieval_sources"])

    def test_policy_override_controls_large_chat_semantic_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            excluded_store = seed_fixture_store(
                db_path=Path(temp_dir) / "excluded.sqlite3",
                policy=self.base_policy,
                embedding_client=DeterministicEmbeddingClient(),
            )
            included_store = seed_fixture_store(
                db_path=Path(temp_dir) / "included.sqlite3",
                policy=self._fixture_policy(),
                embedding_client=DeterministicEmbeddingClient(),
            )
            settings = included_store.get_semantic_settings()
            excluded_hits = excluded_store.retrieve_semantic(
                query_vector=DeterministicEmbeddingClient().embed_query("디자인 리뷰 옮겨진 내용"),
                limit=3,
                semantic_top_k=3,
                config_signature=excluded_store.get_semantic_settings()["config_signature"],
            )
            included_hits = included_store.retrieve_semantic(
                query_vector=DeterministicEmbeddingClient().embed_query("디자인 리뷰 옮겨진 내용"),
                limit=3,
                semantic_top_k=3,
                config_signature=settings["config_signature"],
            )
        self.assertNotIn(201, [hit["message"]["log_id"] for hit in excluded_hits])
        self.assertEqual(included_hits[0]["message"]["log_id"], 201)

    def test_qwen_query_profile_uses_prompt_name_only_for_queries(self) -> None:
        calls: list[dict[str, str | None]] = []

        class FakeInferenceClient:
            def __init__(self, **_: object) -> None:
                self.provider = None
                self.token = None
                self.headers = {}

            def feature_extraction(self, *, text: str, model: str | None = None, prompt_name: str | None = None):
                calls.append({"text": text, "model": model, "prompt_name": prompt_name})
                return np.array([1.0, 0.0], dtype="float32")

        with patch("tools.live_rag.embedding_client.InferenceClient", FakeInferenceClient):
            client = ExternalEmbeddingClient(model="Qwen/Qwen3-Embedding-8B")
            client.embed_query("회의가 연기됐나요")
            client.embed_documents(["회의 일정 공지"])

        self.assertEqual(calls[0]["prompt_name"], "query")
        self.assertIsNone(calls[1]["prompt_name"])

    def _fixture_policy(self) -> SemanticPolicy:
        allow_chat_ids = tuple(sorted(set(self.base_policy.allow_chat_ids) | {9900}))
        signature = md5_hex(
            {
                "base_policy_signature": self.base_policy.signature,
                "fixture_allow_chat_ids": list(allow_chat_ids),
                "default_max_member_count": self.base_policy.default_max_member_count,
                "deny_chat_ids": list(self.base_policy.deny_chat_ids),
                "chat_overrides": self.base_policy.chat_overrides,
            }
        )
        return SemanticPolicy(
            default_max_member_count=self.base_policy.default_max_member_count,
            allow_chat_ids=allow_chat_ids,
            deny_chat_ids=self.base_policy.deny_chat_ids,
            chat_overrides=self.base_policy.chat_overrides,
            signature=signature,
            source_path=self.base_policy.source_path,
            version=self.base_policy.version,
        )


if __name__ == "__main__":
    unittest.main()
