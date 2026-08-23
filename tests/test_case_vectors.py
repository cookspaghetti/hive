"""Qdrant candidate retrieval and safe live probing tests."""

from types import SimpleNamespace

from hive.case_intelligence import (
    LocalCaseIntelligenceStore,
    build_case_profile,
    build_probe_context,
)
from hive.case_vectors import (
    EmbeddingCompatibilityError,
    HybridCaseIntelligenceStore,
    QdrantCaseVectorIndex,
)
from hive.state import HVI, Message, SessionState


class FakeEmbedder:
    dimension = 3

    def document(self, text):
        return [1.0, 0.0, 0.0]

    def query(self, text):
        return [0.9, 0.1, 0.0]


class FakeQdrant:
    def __init__(self):
        self.created = None
        self.points = []
        self.query = None

    def collection_exists(self, name):
        return False

    def create_collection(self, **kwargs):
        self.created = kwargs

    def upsert(self, **kwargs):
        self.points.extend(kwargs["points"])

    def query_points(self, **kwargs):
        self.query = kwargs
        return SimpleNamespace(
            points=[
                SimpleNamespace(
                    score=0.81,
                    payload={"case_id": "8d195b38-c1c3-4e00-ac1a-bcc5851ea2be"},
                )
            ]
        )


def _profile(case_id: str, account: str = "12345678", phone: str = "") -> dict:
    indicators = [
        {
            "kind": "bank_account",
            "value": account,
            "confidence": 0.8,
            "source_msg_id": 1,
            "extractor": "regex",
        }
    ]
    if phone:
        indicators.append(
            {
                "kind": "phone",
                "value": phone,
                "confidence": 0.8,
                "source_msg_id": 2,
                "extractor": "regex",
            }
        )
    return build_case_profile(
        {
            "id": case_id,
            "peer_id": 919,
            "ended_ts": 20,
            "verdict": "likely_scam",
            "score": 0.9,
            "messages": [{"role": "stranger", "text": "invest now", "msg_id": 1}],
            "hvi_items": indicators,
            "signal_trail": [],
            "sandbox_results": [],
            "analysis": {
                "id": "run",
                "schema_version": 1,
                "created_ts": 20,
                "transcript_sha256": "abc",
            },
        }
    )


def test_qdrant_index_uses_separate_collection_and_candidate_label():
    client = FakeQdrant()
    index = QdrantCaseVectorIndex(
        "http://qdrant:6333",
        embedder=FakeEmbedder(),
        client=client,
    )
    profile = _profile("8514213f-a1eb-4986-a2dc-bd3fa196ea96")

    index.upsert(profile)
    results = index.search(profile)

    assert client.created["collection_name"] == "hive_cases"
    assert client.created["vectors_config"].size == 3
    assert client.points[0].payload["case_id"] == profile["case_id"]
    assert client.points[0].payload["vector_schema_version"] == 2
    assert client.points[0].payload["indicator_kinds"] == ["bank_account"]
    fingerprint = client.points[0].payload["embedding_fingerprint"]
    assert len(fingerprint) == 64
    assert "methods" not in client.points[0].payload
    assert client.query["query_filter"].must[0].match.value == fingerprint
    assert results[0]["relationship"] == "script_similarity"
    assert results[0]["candidate_only"] is True
    assert results[0]["score"] == 0.81


def test_case_index_readiness_runs_a_real_vector_query():
    client = FakeQdrant()
    index = QdrantCaseVectorIndex(
        "http://qdrant:6333",
        embedder=FakeEmbedder(),
        client=client,
    )

    index.ensure_ready()

    assert client.created["collection_name"] == "hive_cases"
    assert client.query["collection_name"] == "hive_cases"
    assert client.query["with_payload"] is False
    assert len(client.query["query_filter"].must[0].match.value) == 64


def test_existing_unversioned_collection_is_rejected():
    client = FakeQdrant()
    client.collection_exists = lambda _name: True
    client.get_collection = lambda _name: SimpleNamespace(
        config=SimpleNamespace(
            params=SimpleNamespace(vectors=SimpleNamespace(size=3)),
            metadata=None,
        )
    )
    index = QdrantCaseVectorIndex(
        "http://qdrant:6333",
        embedder=FakeEmbedder(),
        client=client,
    )

    try:
        index.ensure_ready()
    except EmbeddingCompatibilityError as exc:
        assert "unversioned" in str(exc)
        assert "hive-case-reindex" in str(exc)
    else:
        raise AssertionError("unversioned collection must be rejected")


def test_hybrid_store_keeps_exact_edge_stronger_than_semantic_candidate(tmp_path):
    relational = LocalCaseIntelligenceStore(tmp_path)
    first = _profile("8514213f-a1eb-4986-a2dc-bd3fa196ea96")
    second = _profile("8d195b38-c1c3-4e00-ac1a-bcc5851ea2be")
    relational.index(first)
    relational.index(second)

    class Vectors:
        def upsert(self, profile):
            return None

        def search(self, profile, limit=5):
            return [
                {
                    "case_id": profile["case_id"],
                    "related_case_id": second["case_id"],
                    "relationship": "script_similarity",
                    "score": 0.8,
                    "reasons": [],
                    "candidate_only": True,
                }
            ]

    hybrid = HybridCaseIntelligenceStore(relational, Vectors())

    related = hybrid.related(first["case_id"])

    assert related[0]["relationship"] == "shared_identifier"
    assert related[0]["score"] == 0.95
    assert related[0]["semantic_score"] == 0.8
    assert "candidate_only" not in related[0]


def test_hybrid_store_does_not_semantically_query_benign_profiles(tmp_path):
    relational = LocalCaseIntelligenceStore(tmp_path)
    benign = _profile("8514213f-a1eb-4986-a2dc-bd3fa196ea96")
    benign["verdict"] = "likely_benign"
    related = _profile("8d195b38-c1c3-4e00-ac1a-bcc5851ea2be")
    relational.index(benign)
    relational.index(related)

    class Vectors:
        def search(self, profile, limit=5):
            raise AssertionError("benign profiles must not query semantic candidates")

    hybrid = HybridCaseIntelligenceStore(relational, Vectors())

    matches = hybrid.related(benign["case_id"])

    assert matches[0]["relationship"] == "shared_identifier"
    assert "semantic_score" not in matches[0]


def test_live_probe_context_requests_only_missing_identifier_types(tmp_path):
    store = LocalCaseIntelligenceStore(tmp_path)
    previous = _profile(
        "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
        phone="+60 12-345 6789",
    )
    store.index(previous)
    session = SessionState(peer_id=77, persona="confused_elderly")
    session.verdict = "likely_scam"
    session.verdict_score = 0.8
    session.messages.append(Message("stranger", "invest now", 10, 1))
    session.hvis.append(HVI("bank_account", "12345678", 1, 0.8, "regex"))

    context, matches = build_probe_context(session, store)

    assert matches[0]["relationship"] == "shared_identifier"
    assert "exact shared bank account" in context
    assert "Useful missing identifier types: phone" in context
    assert "+60 12-345 6789" not in context
    assert "Never mention prior cases" in context
