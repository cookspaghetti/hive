"""Safe, resumable case-vector reindex tests."""

from types import SimpleNamespace

import pytest

from hive.case_intelligence import build_case_profile
from hive.case_reindex import reindex_case_vectors


class FakeEmbedder:
    dimension = 3

    def document(self, text):
        return [1.0, 0.0, 0.0]

    def query(self, text):
        return [1.0, 0.0, 0.0]


class FakeStore:
    profiles: list[dict[str, object]] = []

    def __init__(self, _database_url):
        pass

    def list_profiles(self):
        return list(self.profiles)


class FakeQdrant:
    def __init__(self):
        self.points = []
        self.metadata = None
        self.created = False

    def collection_exists(self, _name):
        return self.created

    def create_collection(self, **kwargs):
        self.created = True
        self.metadata = kwargs["metadata"]

    def get_collection(self, _name):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(vectors=SimpleNamespace(size=3)),
                metadata=self.metadata,
            )
        )

    def upsert(self, **kwargs):
        incoming = kwargs["points"]
        ids = {point.id for point in incoming}
        self.points = [point for point in self.points if point.id not in ids] + incoming

    def scroll(self, **kwargs):
        return list(self.points), None

    def delete(self, **kwargs):
        ids = set(kwargs["points_selector"].points)
        self.points = [point for point in self.points if point.id not in ids]

    def update_collection(self, **kwargs):
        self.metadata = kwargs["metadata"]


def _profile(case_id: str, *, verdict: str = "likely_scam") -> dict:
    return build_case_profile(
        {
            "id": case_id,
            "peer_id": 919,
            "ended_ts": 20,
            "verdict": verdict,
            "score": 0.9 if verdict == "likely_scam" else 0.1,
            "messages": [{"role": "stranger", "text": "invest now", "msg_id": 1}],
            "hvi_items": [],
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


def test_reindex_uses_authoritative_profiles_and_removes_stale_points(monkeypatch):
    eligible = _profile("8514213f-a1eb-4986-a2dc-bd3fa196ea96")
    benign = _profile(
        "8d195b38-c1c3-4e00-ac1a-bcc5851ea2be",
        verdict="likely_benign",
    )
    FakeStore.profiles = [eligible, benign]
    client = FakeQdrant()
    client.created = True
    client.points = [
        SimpleNamespace(id="7ad15c38-c1c3-4e00-ac1a-bcc5851ea2be", payload={"case_id": "stale"})
    ]
    monkeypatch.setattr("hive.case_reindex.PostgresCaseIntelligenceStore", FakeStore)
    monkeypatch.setattr("hive.case_reindex.audit_event", lambda *args, **kwargs: None)

    result = reindex_case_vectors(
        "postgresql://test",
        "http://qdrant",
        client=client,
        embedder=FakeEmbedder(),
    )

    assert result["source_profiles"] == 2
    assert result["eligible_profiles"] == 1
    assert result["upserted_points"] == 1
    assert result["deleted_stale_points"] == 1
    assert result["verified_points"] == 1
    assert result["valid"] is True
    assert client.points[0].payload["case_id"] == eligible["case_id"]
    assert (
        client.points[0].payload["embedding_fingerprint"]
        == result["embedding_fingerprint"]
        == client.metadata["hive_embedding_fingerprint"]
    )


def test_reindex_refuses_empty_authoritative_store(monkeypatch):
    FakeStore.profiles = []
    monkeypatch.setattr("hive.case_reindex.PostgresCaseIntelligenceStore", FakeStore)

    with pytest.raises(RuntimeError, match="refusing to empty"):
        reindex_case_vectors(
            "postgresql://test",
            "http://qdrant",
            client=FakeQdrant(),
            embedder=FakeEmbedder(),
        )


def test_reindex_dry_run_does_not_mutate_qdrant(monkeypatch):
    FakeStore.profiles = [_profile("8514213f-a1eb-4986-a2dc-bd3fa196ea96")]
    client = FakeQdrant()
    monkeypatch.setattr("hive.case_reindex.PostgresCaseIntelligenceStore", FakeStore)

    result = reindex_case_vectors(
        "postgresql://test",
        "http://qdrant",
        client=client,
        embedder=FakeEmbedder(),
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["eligible_profiles"] == 1
    assert client.created is False
    assert client.points == []
