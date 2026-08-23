"""Measure the deployed Qdrant scam-pattern index with temporary cases."""

from __future__ import annotations

import json
import time
from uuid import uuid4

from hive.case_intelligence import build_case_profile
from hive.case_vectors import QdrantCaseVectorIndex
from hive.config import load_settings


def profile(case_id: str, text: str, account: str) -> dict:
    return build_case_profile(
        {
            "id": case_id,
            "peer_id": 987_654_321,
            "ended_ts": time.time(),
            "verdict": "likely_scam",
            "score": 0.9,
            "messages": [{"role": "stranger", "text": text, "msg_id": 1}],
            "hvi_items": [
                {
                    "kind": "bank_account",
                    "value": account,
                    "confidence": 0.95,
                    "source_msg_id": 1,
                    "extractor": "benchmark",
                }
            ],
            "signal_trail": [
                {
                    "contributions": [
                        {"reason": "soft:investment_framing", "confidence": 0.9},
                        {"reason": "soft:urgency", "confidence": 0.8},
                    ]
                }
            ],
            "sandbox_results": [],
            "analysis": {
                "id": f"benchmark-{case_id}",
                "schema_version": 1,
                "created_ts": time.time(),
                "transcript_sha256": "synthetic-benchmark",
            },
        }
    )


def main() -> None:
    settings = load_settings()
    index = QdrantCaseVectorIndex(
        settings.qdrant_url,
        similarity_threshold=settings.case_similarity_threshold,
        embedding_model=settings.case_embedding_model,
    )
    reference = profile(
        str(uuid4()),
        "Urgent private investment offer. Transfer today to secure the profit slot.",
        "1234567890",
    )
    query = profile(
        str(uuid4()),
        "Limited investment opportunity. Pay now before the high-return slot closes.",
        "9087654321",
    )
    ready_started = time.perf_counter()
    index.ensure_ready()
    ready_ms = (time.perf_counter() - ready_started) * 1_000
    try:
        upsert_started = time.perf_counter()
        index.upsert(reference)
        upsert_ms = (time.perf_counter() - upsert_started) * 1_000
        search_started = time.perf_counter()
        matches = index.search(query, limit=5)
        search_ms = (time.perf_counter() - search_started) * 1_000
    finally:
        index.delete(reference["case_id"], peer_id=reference["peer_id"])
        index.delete(query["case_id"], peer_id=query["peer_id"])
    print(
        json.dumps(
            {
                "ready_ms": round(ready_ms, 2),
                "upsert_ms": round(upsert_ms, 2),
                "search_ms": round(search_ms, 2),
                "matches": matches,
                "temporary_cases_removed": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
