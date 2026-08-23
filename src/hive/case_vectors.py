"""Qdrant candidate retrieval layered over authoritative case relationships."""

from __future__ import annotations

import hashlib
import json
import threading
import warnings
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from hive.audit import audit_event
from hive.case_intelligence import CaseIntelligenceStore

EMBEDDING_SCHEMA_VERSION = 1
EMBEDDING_FINGERPRINT_PAYLOAD_KEY = "embedding_fingerprint"
_MEAN_POOLING_MODELS = frozenset({"sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"})


class EmbeddingCompatibilityError(RuntimeError):
    """The configured embedder does not match the collection's vector space."""


class TextEmbedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def document(self, text: str) -> list[float]: ...

    def query(self, text: str) -> list[float]: ...


class FastEmbedder:
    _cache_lock = threading.Lock()
    _embedding_lock = threading.Lock()
    _models: dict[str, Any] = {}
    _dimensions: dict[str, int] = {}

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._dimension: int | None = None

    def _load(self):
        if self._model is None:
            with self._cache_lock:
                if self.model_name not in self._models:
                    with warnings.catch_warnings():
                        # These upstream notices describe choices HIVE already
                        # controls through its pin and embedding fingerprint.
                        warnings.filterwarnings(
                            "ignore",
                            message=(
                                r"The model .* now uses mean pooling instead of CLS "
                                r"embedding\..*"
                            ),
                            category=UserWarning,
                        )
                        warnings.filterwarnings(
                            "ignore",
                            message=r"Cannot enable progress bars: environment variable .*",
                            category=UserWarning,
                        )
                        from fastembed import TextEmbedding

                        supported = {
                            item["model"]: item for item in TextEmbedding.list_supported_models()
                        }
                        if self.model_name not in supported:
                            raise ValueError(f"unsupported FastEmbed model: {self.model_name}")
                        self._dimensions[self.model_name] = int(supported[self.model_name]["dim"])
                        self._models[self.model_name] = TextEmbedding(
                            model_name=self.model_name,
                            lazy_load=True,
                        )
                self._dimension = self._dimensions[self.model_name]
                self._model = self._models[self.model_name]
        return self._model

    @property
    def dimension(self) -> int:
        self._load()
        return int(self._dimension or 0)

    def document(self, text: str) -> list[float]:
        model = self._load()
        prefix = "passage: " if "e5" in self.model_name.lower() else ""
        with self._embedding_lock:
            return list(next(model.embed([f"{prefix}{text}"])))

    def query(self, text: str) -> list[float]:
        model = self._load()
        prefix = "query: " if "e5" in self.model_name.lower() else ""
        with self._embedding_lock:
            return list(next(model.embed([f"{prefix}{text}"])))

    @property
    def descriptor(self) -> dict[str, Any]:
        try:
            provider_version = version("fastembed")
        except PackageNotFoundError:  # pragma: no cover - dependency is required at runtime
            provider_version = "unknown"
        uses_e5_prefixes = "e5" in self.model_name.lower()
        return {
            "schema_version": EMBEDDING_SCHEMA_VERSION,
            "provider": "fastembed",
            "provider_version": provider_version,
            "model": self.model_name,
            "pooling": ("mean" if self.model_name in _MEAN_POOLING_MODELS else "model_default"),
            "document_prefix": "passage: " if uses_e5_prefixes else "",
            "query_prefix": "query: " if uses_e5_prefixes else "",
            "dimension": self.dimension,
            "distance": "cosine",
        }


def _embedding_descriptor(embedder: TextEmbedder, model_name: str) -> dict[str, Any]:
    descriptor = getattr(embedder, "descriptor", None)
    if isinstance(descriptor, dict):
        return dict(descriptor)
    embedder_type = type(embedder)
    return {
        "schema_version": EMBEDDING_SCHEMA_VERSION,
        "provider": f"{embedder_type.__module__}.{embedder_type.__qualname__}",
        "provider_version": "injected",
        "model": model_name,
        "pooling": "embedder_defined",
        "document_prefix": "",
        "query_prefix": "",
        "dimension": int(embedder.dimension),
        "distance": "cosine",
    }


def _embedding_fingerprint(descriptor: dict[str, Any]) -> str:
    canonical = json.dumps(
        descriptor,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def eligible_for_semantic_index(profile: dict[str, Any]) -> bool:
    """Return whether a sealed case may enter candidate-only semantic retrieval."""
    return profile.get("verdict") != "likely_benign" and bool(
        float(profile.get("score") or 0) >= 0.5 or profile.get("indicators")
    )


def _point_id(case_id: str) -> str:
    try:
        return str(UUID(case_id))
    except ValueError:
        return str(uuid5(NAMESPACE_URL, f"hive-case:{case_id}"))


class QdrantCaseVectorIndex:
    def __init__(
        self,
        qdrant_url: str,
        *,
        collection: str = "hive_cases",
        similarity_threshold: float = 0.72,
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        embedder: TextEmbedder | None = None,
        client: Any = None,
        strict_compatibility: bool = True,
    ) -> None:
        self.collection = collection
        self.similarity_threshold = similarity_threshold
        self.embedding_model = embedding_model
        self.embedder = embedder or FastEmbedder(embedding_model)
        if client is None:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=qdrant_url)
        self.client = client
        self.strict_compatibility = strict_compatibility
        self._initialized = False
        self._descriptor: dict[str, Any] | None = None
        self._fingerprint: str | None = None

    @property
    def embedding_descriptor(self) -> dict[str, Any]:
        if self._descriptor is None:
            self._descriptor = _embedding_descriptor(
                self.embedder,
                self.embedding_model,
            )
        return dict(self._descriptor)

    @property
    def embedding_fingerprint(self) -> str:
        if self._fingerprint is None:
            self._fingerprint = _embedding_fingerprint(self.embedding_descriptor)
        return self._fingerprint

    def ensure_ready(self) -> None:
        """Load the embedder and create/query the dedicated case collection."""
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self._ensure()
        vector = self.embedder.query("HIVE scam-pattern readiness probe")
        if len(vector) != self.embedder.dimension:
            raise RuntimeError("case embedding dimension changed during readiness probe")
        self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key=EMBEDDING_FINGERPRINT_PAYLOAD_KEY,
                        match=MatchValue(value=self.embedding_fingerprint),
                    )
                ]
            ),
            limit=1,
            with_payload=False,
        )

    def _ensure(self) -> None:
        if self._initialized:
            return
        if not self.client.collection_exists(self.collection):
            from qdrant_client.models import Distance, VectorParams

            self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(
                    size=self.embedder.dimension,
                    distance=Distance.COSINE,
                ),
                metadata={
                    "hive_embedding_descriptor": self.embedding_descriptor,
                    "hive_embedding_fingerprint": self.embedding_fingerprint,
                },
            )
        else:
            info = self.client.get_collection(self.collection)
            vectors = info.config.params.vectors
            size = getattr(vectors, "size", None)
            if size is None and isinstance(vectors, dict):
                size = next(iter(vectors.values())).size
            if int(size or 0) != self.embedder.dimension:
                raise EmbeddingCompatibilityError(
                    "Qdrant case-vector dimension does not match the configured embedder; "
                    "recreate the derived collection under the approved migration "
                    "procedure, then run hive-case-reindex"
                )
            metadata = dict(getattr(info.config, "metadata", None) or {})
            stored = str(metadata.get("hive_embedding_fingerprint") or "")
            if self.strict_compatibility and stored != self.embedding_fingerprint:
                state = "unversioned" if not stored else "incompatible"
                raise EmbeddingCompatibilityError(
                    f"Qdrant case-vector collection is {state}; run hive-case-reindex "
                    "before enabling semantic retrieval"
                )
        self._initialized = True

    def mark_compatible(self) -> None:
        """Mark collection metadata only after a complete verified reindex."""
        self.client.update_collection(
            collection_name=self.collection,
            metadata={
                "hive_embedding_descriptor": self.embedding_descriptor,
                "hive_embedding_fingerprint": self.embedding_fingerprint,
            },
        )

    def upsert(self, profile: dict[str, Any]) -> None:
        from qdrant_client.models import PointStruct

        self._ensure()
        case_id = str(profile["case_id"])
        self.client.upsert(
            collection_name=self.collection,
            points=[
                PointStruct(
                    id=_point_id(case_id),
                    vector=self.embedder.document(str(profile.get("embedding_text") or "")),
                    payload={
                        "case_id": case_id,
                        "analysis_run_id": profile.get("analysis_run_id"),
                        "transcript_sha256": profile.get("transcript_sha256"),
                        "verdict": profile.get("verdict"),
                        "score": profile.get("score"),
                        "vector_schema_version": (
                            profile.get("scam_vector") or {}
                        ).get("schema_version", 1),
                        "method_keys": (
                            profile.get("scam_vector") or {}
                        ).get("method_keys", []),
                        "indicator_kinds": (
                            profile.get("scam_vector") or {}
                        ).get("indicator_kinds", []),
                        EMBEDDING_FINGERPRINT_PAYLOAD_KEY: self.embedding_fingerprint,
                    },
                )
            ],
            wait=True,
        )
        audit_event(
            "semantic_case_index",
            "case_vector_upserted",
            component="case_vectors.qdrant",
            payload={
                "case_id": case_id,
                "analysis_run_id": profile.get("analysis_run_id"),
                "collection": self.collection,
            },
            peer_id=int(profile["peer_id"]),
        )

    def delete(self, case_id: str, *, peer_id: int = 0) -> None:
        """Remove one derived vector; the authoritative case remains relational."""
        from qdrant_client.models import PointIdsList

        self._ensure()
        self.client.delete(
            collection_name=self.collection,
            points_selector=PointIdsList(points=[_point_id(case_id)]),
            wait=True,
        )
        audit_event(
            "semantic_case_index",
            "case_vector_deleted",
            component="case_vectors.qdrant",
            payload={"case_id": case_id, "collection": self.collection},
            peer_id=peer_id,
        )

    def search(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        self._ensure()
        case_id = str(profile["case_id"])
        response = self.client.query_points(
            collection_name=self.collection,
            query=self.embedder.query(str(profile.get("embedding_text") or "")),
            query_filter=Filter(
                must=[
                    FieldCondition(
                        key=EMBEDDING_FINGERPRINT_PAYLOAD_KEY,
                        match=MatchValue(value=self.embedding_fingerprint),
                    )
                ],
                must_not=[FieldCondition(key="case_id", match=MatchValue(value=case_id))],
            ),
            limit=limit,
            with_payload=True,
            score_threshold=self.similarity_threshold,
        )
        points = getattr(response, "points", response)
        rows = []
        for point in points:
            payload = dict(getattr(point, "payload", {}) or {})
            related_case_id = str(payload.get("case_id") or "")
            if not related_case_id:
                continue
            rows.append(
                {
                    "case_id": case_id,
                    "related_case_id": related_case_id,
                    "relationship": "script_similarity",
                    "score": round(float(point.score), 4),
                    "reasons": [
                        {
                            "kind": "semantic_similarity",
                            "match": "candidate_only",
                            "value": "Similar scam script and behavioral pattern",
                        }
                    ],
                    "candidate_only": True,
                }
            )
        audit_event(
            "semantic_case_retrieval",
            "similar_cases_retrieved",
            component="case_vectors.qdrant",
            payload={
                "case_id": case_id,
                "collection": self.collection,
                "matches": rows,
            },
            peer_id=int(profile["peer_id"]),
        )
        return rows


class HybridCaseIntelligenceStore:
    def __init__(
        self,
        relational: CaseIntelligenceStore,
        vectors: QdrantCaseVectorIndex,
    ) -> None:
        self.relational = relational
        self.vectors = vectors

    def ensure_ready(self) -> None:
        self.vectors.ensure_ready()

    @staticmethod
    def _eligible(profile: dict[str, Any]) -> bool:
        return eligible_for_semantic_index(profile)

    def backfill_vector(self, profile: dict[str, Any]) -> None:
        """Synchronise the derived vector without rewriting relational evidence."""
        if self._eligible(profile):
            self.vectors.upsert(profile)
        else:
            self.vectors.delete(
                str(profile["case_id"]),
                peer_id=int(profile["peer_id"]),
            )

    def index(self, profile: dict[str, Any]) -> dict[str, Any]:
        stored = self.relational.index(profile)
        if self._eligible(profile):
            try:
                self.vectors.upsert(profile)
            except Exception as exc:  # noqa: BLE001 - PostgreSQL/local profile remains authoritative
                audit_event(
                    "semantic_case_index",
                    "case_vector_upsert_failed",
                    component="case_vectors.hybrid",
                    payload={"case_id": profile["case_id"], "error": str(exc)},
                    peer_id=int(profile["peer_id"]),
                    level="error",
                )
        else:
            try:
                self.vectors.delete(
                    str(profile["case_id"]),
                    peer_id=int(profile["peer_id"]),
                )
            except Exception as exc:  # noqa: BLE001 - relational profile remains authoritative
                audit_event(
                    "semantic_case_index",
                    "case_vector_delete_failed",
                    component="case_vectors.hybrid",
                    payload={"case_id": profile["case_id"], "error": str(exc)},
                    peer_id=int(profile["peer_id"]),
                    level="error",
                )
        return stored

    def get(self, case_id: str) -> dict[str, Any] | None:
        return self.relational.get(case_id)

    def related(self, case_id: str) -> list[dict[str, Any]]:
        profile = self.relational.get(case_id)
        if profile is None:
            return []
        return self._merge(
            self.relational.related(case_id),
            self._semantic(profile) if self._eligible(profile) else [],
        )

    def match(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        return self._merge(
            self.relational.match(profile, limit),
            self._semantic(profile, limit) if self._eligible(profile) else [],
        )[:limit]

    def _semantic(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        try:
            return self.vectors.search(profile, limit)
        except Exception as exc:  # noqa: BLE001 - exact relationships still work without vectors
            audit_event(
                "semantic_case_retrieval",
                "similar_case_retrieval_failed",
                component="case_vectors.hybrid",
                payload={"case_id": profile["case_id"], "error": str(exc)},
                peer_id=int(profile["peer_id"]),
                level="error",
            )
            return []

    @staticmethod
    def _merge(
        exact: list[dict[str, Any]],
        semantic: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged = {row["related_case_id"]: dict(row) for row in semantic}
        for row in exact:
            related = row["related_case_id"]
            semantic_row = merged.get(related)
            combined = dict(row)
            if semantic_row:
                combined["semantic_score"] = semantic_row["score"]
            merged[related] = combined
        return sorted(merged.values(), key=lambda row: row["score"], reverse=True)
