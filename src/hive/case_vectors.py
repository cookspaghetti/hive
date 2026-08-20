"""Qdrant candidate retrieval layered over authoritative case relationships."""

from __future__ import annotations

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
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name
        self._model = None
        self._dimension: int | None = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding

            supported = {
                item["model"]: item for item in TextEmbedding.list_supported_models()
            }
            if self.model_name not in supported:
                raise ValueError(f"unsupported FastEmbed model: {self.model_name}")
            self._dimension = int(supported[self.model_name]["dim"])
            self._model = TextEmbedding(model_name=self.model_name, lazy_load=True)
        return self._model

    @property
    def dimension(self) -> int:
        self._load()
        return int(self._dimension or 0)

    def document(self, text: str) -> list[float]:
        model = self._load()
        prefix = "passage: " if "e5" in self.model_name.lower() else ""
        return list(next(model.embed([f"{prefix}{text}"])))

    def query(self, text: str) -> list[float]:
        model = self._load()
        prefix = "query: " if "e5" in self.model_name.lower() else ""
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
                        "methods": profile.get("methods") or [],
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

    def index(self, profile: dict[str, Any]) -> dict[str, Any]:
        stored = self.relational.index(profile)
        if profile.get("verdict") != "likely_benign" and (
            float(profile.get("score") or 0) >= 0.5 or profile.get("indicators")
        ):
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
        return stored

    def get(self, case_id: str) -> dict[str, Any] | None:
        return self.relational.get(case_id)

    def related(self, case_id: str) -> list[dict[str, Any]]:
        profile = self.relational.get(case_id)
        if profile is None:
            return []
        return self._merge(
            self.relational.related(case_id),
            self._semantic(profile),
        )

    def match(self, profile: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
        return self._merge(
            self.relational.match(profile, limit),
            self._semantic(profile, limit),
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
