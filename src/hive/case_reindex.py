"""Rebuild the derived Qdrant case-pattern index from authoritative PostgreSQL."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from hive.audit import audit_event
from hive.case_intelligence import PostgresCaseIntelligenceStore
from hive.case_vectors import (
    EMBEDDING_FINGERPRINT_PAYLOAD_KEY,
    QdrantCaseVectorIndex,
    eligible_for_semantic_index,
)
from hive.config import Settings


def _scroll_all(client: Any, collection: str) -> list[Any]:
    rows: list[Any] = []
    offset: Any = None
    while True:
        page, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        rows.extend(page)
        if offset is None:
            return rows


def reindex_case_vectors(
    database_url: str,
    qdrant_url: str,
    *,
    collection: str = "hive_cases",
    embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    allow_empty: bool = False,
    dry_run: bool = False,
    client: Any = None,
    embedder: Any = None,
) -> dict[str, Any]:
    """Synchronise all eligible case vectors and verify exact source coverage."""
    if not database_url:
        raise ValueError("PostgreSQL URL is required")
    if not qdrant_url and client is None:
        raise ValueError("Qdrant URL is required")

    profiles = PostgresCaseIntelligenceStore(database_url).list_profiles()
    eligible = {
        str(profile["case_id"]): profile
        for profile in profiles
        if eligible_for_semantic_index(profile)
    }
    if not profiles and not allow_empty:
        raise RuntimeError(
            "authoritative PostgreSQL has no case profiles; refusing to empty the "
            "derived index without --allow-empty"
        )

    if client is None:
        from qdrant_client import QdrantClient

        client = QdrantClient(url=qdrant_url)
    index = QdrantCaseVectorIndex(
        qdrant_url,
        collection=collection,
        embedding_model=embedding_model,
        embedder=embedder,
        client=client,
        strict_compatibility=False,
    )
    started = time.perf_counter()
    fingerprint = index.embedding_fingerprint
    descriptor = index.embedding_descriptor
    existing = _scroll_all(client, collection) if client.collection_exists(collection) else []
    preview = {
        "collection": collection,
        "source_profiles": len(profiles),
        "eligible_profiles": len(eligible),
        "existing_points": len(existing),
        "embedding_fingerprint": fingerprint,
        "embedding_descriptor": descriptor,
        "dry_run": dry_run,
    }
    if dry_run:
        return preview

    audit_event(
        "semantic_case_reindex",
        "case_vector_reindex_started",
        component="case_reindex",
        payload=preview,
    )
    try:
        for profile in eligible.values():
            index.upsert(profile)

        rows = _scroll_all(client, collection)
        stale_ids = [
            row.id for row in rows if str((row.payload or {}).get("case_id") or "") not in eligible
        ]
        if stale_ids:
            from qdrant_client.models import PointIdsList

            client.delete(
                collection_name=collection,
                points_selector=PointIdsList(points=stale_ids),
                wait=True,
            )

        verified_rows = _scroll_all(client, collection)
        compatible_ids = {
            str((row.payload or {}).get("case_id") or "")
            for row in verified_rows
            if (row.payload or {}).get(EMBEDDING_FINGERPRINT_PAYLOAD_KEY) == fingerprint
        }
        actual_ids = {str((row.payload or {}).get("case_id") or "") for row in verified_rows}
        expected_ids = set(eligible)
        if actual_ids != expected_ids or compatible_ids != expected_ids:
            raise RuntimeError(
                "case-vector reindex verification failed: Qdrant coverage does not "
                "match authoritative eligible PostgreSQL profiles"
            )
        index.mark_compatible()
        metadata = dict(getattr(client.get_collection(collection).config, "metadata", None) or {})
        if metadata.get("hive_embedding_fingerprint") != fingerprint:
            raise RuntimeError("case-vector collection metadata verification failed")
    except Exception as exc:
        audit_event(
            "semantic_case_reindex",
            "case_vector_reindex_failed",
            component="case_reindex",
            payload={
                "collection": collection,
                "embedding_fingerprint": fingerprint,
                "error": str(exc),
            },
            level="error",
        )
        raise

    result = {
        **preview,
        "upserted_points": len(eligible),
        "deleted_stale_points": len(stale_ids),
        "verified_points": len(verified_rows),
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
        "valid": True,
    }
    audit_event(
        "semantic_case_reindex",
        "case_vector_reindex_completed",
        component="case_reindex",
        payload=result,
    )
    return result


def main() -> None:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=settings.database_url)
    parser.add_argument("--qdrant-url", default=settings.qdrant_url)
    parser.add_argument("--collection", default="hive_cases")
    parser.add_argument("--embedding-model", default=settings.case_embedding_model)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            reindex_case_vectors(
                args.database_url,
                args.qdrant_url,
                collection=args.collection,
                embedding_model=args.embedding_model,
                allow_empty=args.allow_empty,
                dry_run=args.dry_run,
            ),
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
