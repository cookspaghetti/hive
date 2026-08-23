"""Qdrant candidate retrieval layered over authoritative case relationships."""

from __future__ import annotations

import threading
from typing import Any, Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from hive.audit import audit_event
from hive.case_intelligence import CaseIntelligenceStore


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
                    from fastembed import TextEmbedding

                    supported = {
                        item["model"]: item for item in TextEmbedding.list_supported_models()
                    }
                    if self.model_name not in supported:
                        raise ValueError(f"unsupported FastEmbed model: {self.model_name}")
                    self._dimensions[self.model_name] = int(
                        supported[self.model_name]["dim"]
                    )
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
    ) -> None:
        self.collection = collection
        self.similarity_threshold = similarity_threshold
        self.embedder = embedder or FastEmbedder(embedding_model)
        if client is None:
            from qdrant_client import QdrantClient

            client = QdrantClient(url=qdrant_url)
        self.client = client
        self._initialized = False

    def ensure_ready(self) -> None:
        """Load the embedder and create/query the dedicated case collection."""
        self._ensure()
        vector = self.embedder.query("HIVE scam-pattern readiness probe")
        if len(vector) != self.embedder.dimension:
            raise RuntimeError("case embedding dimension changed during readiness probe")
        self.client.query_points(
            collection_name=self.collection,
            query=vector,
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
            )
        self._initialized = True

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
                must_not=[FieldCondition(key="case_id", match=MatchValue(value=case_id))]
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
        return profile.get("verdict") != "likely_benign" and bool(
            float(profile.get("score") or 0) >= 0.5 or profile.get("indicators")
        )

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
