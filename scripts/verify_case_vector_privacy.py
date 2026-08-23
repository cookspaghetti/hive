"""Verify that case-vector text/payloads do not contain exact raw identifiers."""

from __future__ import annotations

import json

import psycopg
from qdrant_client import QdrantClient

from hive.case_vectors import QdrantCaseVectorIndex
from hive.config import load_settings

ALLOWED_PAYLOAD_KEYS = {
    "case_id",
    "analysis_run_id",
    "transcript_sha256",
    "verdict",
    "score",
    "vector_schema_version",
    "method_keys",
    "indicator_kinds",
    "embedding_fingerprint",
}


def main() -> None:
    settings = load_settings()
    with psycopg.connect(settings.database_url) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT profile FROM hive_cases")
        profiles = [row[0] for row in cursor.fetchall()]

    text_leaks = 0
    wrong_schema = 0
    for profile in profiles:
        embedding_text = str(profile.get("embedding_text") or "").casefold()
        scam_vector = profile.get("scam_vector") or {}
        if scam_vector.get("schema_version") != 2:
            wrong_schema += 1
        for indicator in profile.get("indicators") or []:
            value = str(indicator.get("value") or "").strip().casefold()
            if len(value) >= 3 and value in embedding_text:
                text_leaks += 1

    qdrant = QdrantClient(url=settings.qdrant_url)
    collections = [item.name for item in qdrant.get_collections().collections]
    rows = []
    aliases = {item.alias_name for item in qdrant.get_aliases().aliases}
    case_collection_present = "hive_cases" in collections or "hive_cases" in aliases
    if case_collection_present:
        rows, _ = qdrant.scroll(
            "hive_cases",
            limit=10_000,
            with_vectors=False,
            with_payload=True,
        )
    payload_key_violations = sum(
        bool(set((row.payload or {}).keys()) - ALLOWED_PAYLOAD_KEYS) for row in rows
    )
    payload_schema_violations = sum(
        (row.payload or {}).get("vector_schema_version") != 2 for row in rows
    )
    expected_embedding_fingerprint = QdrantCaseVectorIndex(
        settings.qdrant_url,
        embedding_model=settings.case_embedding_model,
        client=qdrant,
        strict_compatibility=False,
    ).embedding_fingerprint
    payload_embedding_violations = sum(
        (row.payload or {}).get("embedding_fingerprint") != expected_embedding_fingerprint
        for row in rows
    )
    collection_metadata = (
        dict(qdrant.get_collection("hive_cases").config.metadata or {})
        if case_collection_present
        else {}
    )
    metadata_embedding_match = (
        collection_metadata.get("hive_embedding_fingerprint") == expected_embedding_fingerprint
    )
    result = {
        "postgres_profiles": len(profiles),
        "postgres_schema_v2": len(profiles) - wrong_schema,
        "embedding_exact_identifier_leaks": text_leaks,
        "qdrant_collections": collections,
        "qdrant_case_points": len(rows),
        "qdrant_payload_key_violations": payload_key_violations,
        "qdrant_payload_schema_violations": payload_schema_violations,
        "qdrant_payload_embedding_violations": payload_embedding_violations,
        "qdrant_metadata_embedding_match": metadata_embedding_match,
        "valid": (
            wrong_schema == 0
            and text_leaks == 0
            and "hive_cases" in set(collections) | aliases
            and payload_key_violations == 0
            and payload_schema_violations == 0
            and payload_embedding_violations == 0
            and metadata_embedding_match
        ),
    }
    print(json.dumps(result, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
