"""Model-aware embedding client with query/document separation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from huggingface_hub import InferenceClient
from huggingface_hub.inference._providers import get_provider_helper

try:
    from .semantic_index import DEFAULT_EMBEDDING_MODEL
except ImportError:
    from semantic_index import DEFAULT_EMBEDDING_MODEL


@dataclass(frozen=True)
class EmbeddingProfile:
    """Embedding profile metadata for query/document formatting."""

    name: str
    query_prompt_name: str | None
    query_profile_version: str


QWEN_PROFILE = EmbeddingProfile(
    name="qwen",
    query_prompt_name="query",
    query_profile_version="qwen-query-v1",
)
GENERIC_HF_PROFILE = EmbeddingProfile(
    name="generic_hf",
    query_prompt_name=None,
    query_profile_version="generic-query-v1",
)


class ExternalEmbeddingClient:
    """Wrap Hugging Face embedding inference for documents and queries."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_EMBEDDING_MODEL,
        provider: str | None = None,
        token: str | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self.token = token or os.environ.get("HF_TOKEN")
        self.profile = resolve_embedding_profile(model)
        self.query_profile_version = self.profile.query_profile_version
        self.auth_source = "HF_TOKEN" if self.token else "cached login credentials"
        client_kwargs: dict[str, Any] = {}
        if self.token:
            client_kwargs["token"] = self.token
        if self.provider:
            client_kwargs["provider"] = self.provider
        self._client = InferenceClient(**client_kwargs)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._embed_many(texts, prompt_name=None)

    def embed_query(self, text: str) -> list[float]:
        return self._embed_many([text], prompt_name=self.profile.query_prompt_name)[0]

    def _embed_many(self, texts: list[str], *, prompt_name: str | None) -> list[list[float]]:
        if len(texts) == 1:
            return [self._embed_one(texts[0], prompt_name=prompt_name)]
        try:
            return self._embed_batch(texts, prompt_name=prompt_name)
        except Exception:
            return [self._embed_one(text, prompt_name=prompt_name) for text in texts]

    def _embed_one(self, text: str, *, prompt_name: str | None) -> list[float]:
        return self._coerce_vector(
            self._call_feature_extraction(
                payload=text,
                prompt_name=prompt_name,
            )
        )

    def _embed_batch(self, texts: list[str], *, prompt_name: str | None) -> list[list[float]]:
        payload = self._call_feature_extraction_batch(payload=texts, prompt_name=prompt_name)
        vectors = self._coerce_batch(payload, expected_count=len(texts))
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding batch response count did not match the number of texts.")
        return vectors

    def _call_feature_extraction(self, *, payload: str, prompt_name: str | None) -> Any:
        try:
            return self._client.feature_extraction(
                text=payload,
                model=self.model,
                prompt_name=prompt_name,
            )
        except Exception as error:  # pragma: no cover - network/provider behavior.
            provider = self.provider or "default"
            raise RuntimeError(
                f"Failed to embed text with model `{self.model}` via provider `{provider}` "
                f"using {self.auth_source}: {error}"
            ) from error

    def _call_feature_extraction_batch(self, *, payload: list[str], prompt_name: str | None) -> Any:
        try:
            provider_helper = get_provider_helper(self._client.provider, task="feature-extraction", model=self.model)
            request_parameters = provider_helper.prepare_request(
                inputs=payload,
                parameters={"normalize": None, "prompt_name": prompt_name, "truncate": None, "truncation_direction": None},
                headers=self._client.headers,
                model=self.model,
                api_key=self._client.token,
            )
            response = self._client._inner_post(request_parameters)
            return provider_helper.get_response(response)
        except Exception as error:  # pragma: no cover - network/provider behavior.
            provider = self.provider or "default"
            raise RuntimeError(
                f"Failed to batch-embed texts with model `{self.model}` via provider `{provider}` "
                f"using {self.auth_source}: {error}"
            ) from error

    def _coerce_batch(self, payload: Any, *, expected_count: int) -> list[list[float]]:
        if hasattr(payload, "tolist"):
            payload = payload.tolist()
        payload = self._unwrap_payload(payload)
        if expected_count == 1 and self._is_vector(payload):
            return [self._vector_to_floats(payload)]
        if isinstance(payload, list) and len(payload) == expected_count and all(self._is_vector(item) for item in payload):
            return [self._vector_to_floats(item) for item in payload]
        raise RuntimeError("Embedding response shape was not a 2D batch of vectors.")

    def _coerce_vector(self, payload: Any) -> list[float]:
        if hasattr(payload, "tolist"):
            payload = payload.tolist()
        payload = self._unwrap_payload(payload)
        if isinstance(payload, list) and len(payload) == 1 and self._is_vector(payload[0]):
            payload = payload[0]
        if self._is_vector(payload):
            return self._vector_to_floats(payload)
        raise RuntimeError(
            "Embedding response shape was not a 1D vector. "
            "Try a provider/model pair that serves sentence embeddings."
        )

    def _unwrap_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            for key in ("embeddings", "data", "vector"):
                value = payload.get(key)
                if value is not None:
                    return value
        return payload

    @staticmethod
    def _is_vector(value: Any) -> bool:
        return isinstance(value, list) and bool(value) and all(isinstance(item, (int, float)) for item in value)

    @staticmethod
    def _vector_to_floats(value: list[float]) -> list[float]:
        return [float(item) for item in value]


def resolve_embedding_profile(model: str) -> EmbeddingProfile:
    normalized_model = model.lower()
    if "qwen3-embedding" in normalized_model or normalized_model.startswith("qwen/"):
        return QWEN_PROFILE
    return GENERIC_HF_PROFILE
