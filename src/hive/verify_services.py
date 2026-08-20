"""Run transient PostgreSQL and Qdrant integration checks with cleanup."""

from __future__ import annotations

import argparse
import json
import uuid
from typing import Any

from hive.case_intelligence import PostgresCaseIntelligenceStore, build_case_profile
from hive.case_vectors import QdrantCaseVectorIndex
from hive.config import Settings


class _FixedEmbedder:
    """Small deterministic vector source that avoids model downloads in checks."""

    dimension = 3

    def document(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    def query(self, text: str) -> list[float]:
        return [1.0, 0.0, 0.0]


def _profile(case_id: str, account: str, script: str) -> dict[str, Any]:
    return build_case_profile(
        {
            "id": case_id,
            "peer_id": 0,
            "ended_ts": 1,
            "verdict": "likely_scam",
            "score": 0.9,
            "messages": [{"role": "stranger", "text": script, "msg_id": 1}],
            "hvi_items": [
                {
                    "kind": "bank_account",
                    "value": account,
                    "confidence": 0.9,
                    "source_msg_id": 1,
                    "extractor": "service_verifier",
                }
            ],
            "signal_trail": [],
            "sandbox_results": [],
            "analysis": {
                "id": f"verify-{case_id}",
                "schema_version": 1,
                "created_ts": 1,
                "transcript_sha256": "service-verification",
            },
        }
    )


def verify_postgres(database_url: str) -> dict[str, Any]:
    import psycopg

    case_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    profiles = [
        _profile(case_ids[0], "998877665544", "synthetic verification script one"),
        _profile(case_ids[1], "998877665544", "synthetic verification script two"),
    ]
    store = PostgresCaseIntelligenceStore(database_url)
    try:
        for profile in profiles:
            store.index(profile)
        stored = store.get(case_ids[0])
        related = store.related(case_ids[0])
        if stored is None or stored.get("case_id") != case_ids[0]:
            raise RuntimeError("PostgreSQL case profile round-trip failed")
        if not any(
            row.get("related_case_id") == case_ids[1]
            and row.get("relationship") == "shared_identifier"
            for row in related
        ):
            raise RuntimeError("PostgreSQL exact relationship check failed")
        return {"ok": True, "profiles": 2, "relationships": len(related)}
    finally:
        with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM hive_cases WHERE case_id IN (%s, %s)",
                (case_ids[0], case_ids[1]),
            )


def verify_qdrant(qdrant_url: str) -> dict[str, Any]:
    from qdrant_client import QdrantClient

    collection = f"hive_verify_{uuid.uuid4().hex}"
    case_ids = [str(uuid.uuid4()), str(uuid.uuid4())]
    client = QdrantClient(url=qdrant_url)
    index = QdrantCaseVectorIndex(
        qdrant_url,
        collection=collection,
        similarity_threshold=0.5,
        embedder=_FixedEmbedder(),
        client=client,
    )
    try:
        first = _profile(case_ids[0], "112233445566", "synthetic vector script one")
        second = _profile(case_ids[1], "665544332211", "synthetic vector script two")
        index.upsert(first)
        index.upsert(second)
        matches = index.search(second)
        if not any(
            row.get("related_case_id") == case_ids[0]
            and row.get("relationship") == "script_similarity"
            and row.get("candidate_only") is True
            for row in matches
        ):
            raise RuntimeError("Qdrant similarity round-trip failed")
        return {"ok": True, "points": 2, "matches": len(matches)}
    finally:
        if client.collection_exists(collection):
            client.delete_collection(collection_name=collection)


def verify_services(database_url: str, qdrant_url: str) -> dict[str, Any]:
    if not database_url:
        raise ValueError("PostgreSQL URL is required")
    if not qdrant_url:
        raise ValueError("Qdrant URL is required")
    return {
        "postgres": verify_postgres(database_url),
        "qdrant": verify_qdrant(qdrant_url),
    }


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    args = parser.parse_args()
    print(
        json.dumps(
            verify_services(args.database_url, args.qdrant_url),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
