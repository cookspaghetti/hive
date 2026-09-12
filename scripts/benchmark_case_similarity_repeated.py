"""Repeat the deployed Qdrant scam-pattern benchmark in one warm process."""

from __future__ import annotations

import json
import statistics
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


def summarize(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, max(0, int(0.95 * len(ordered) + 0.9999) - 1))
    return {
        "min_ms": round(ordered[0], 2),
        "mean_ms": round(statistics.fmean(ordered), 2),
        "median_ms": round(statistics.median(ordered), 2),
        "p95_ms": round(ordered[p95_index], 2),
        "max_ms": round(ordered[-1], 2),
    }


def summarize_score(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "minimum": round(ordered[0], 4),
        "mean": round(statistics.fmean(ordered), 4),
        "median": round(statistics.median(ordered), 4),
        "maximum": round(ordered[-1], 4),
    }


def main() -> None:
    settings = load_settings()
    index = QdrantCaseVectorIndex(
        settings.qdrant_url,
        similarity_threshold=settings.case_similarity_threshold,
        embedding_model=settings.case_embedding_model,
    )
    ready_started = time.perf_counter()
    index.ensure_ready()
    ready_ms = (time.perf_counter() - ready_started) * 1_000

    repeats = 10
    upsert_values: list[float] = []
    search_values: list[float] = []
    target_scores: list[float] = []
    target_ranks: list[int] = []
    rows: list[dict[str, object]] = []
    for repeat in range(1, repeats + 1):
        reference = profile(
            str(uuid4()),
            "Urgent private investment offer. Transfer today to secure the profit slot.",
            f"12345678{repeat:02d}",
        )
        query = profile(
            str(uuid4()),
            "Limited investment opportunity. Pay now before the high-return slot closes.",
            f"90876543{repeat:02d}",
        )
        try:
            upsert_started = time.perf_counter()
            index.upsert(reference)
            upsert_ms = (time.perf_counter() - upsert_started) * 1_000

            search_started = time.perf_counter()
            matches = index.search(query, limit=5)
            search_ms = (time.perf_counter() - search_started) * 1_000
            target_rank = next(
                (
                    position
                    for position, match in enumerate(matches, start=1)
                    if match.get("related_case_id") == reference["case_id"]
                ),
                0,
            )
            target_score = next(
                (
                    float(match["score"])
                    for match in matches
                    if match.get("related_case_id") == reference["case_id"]
                ),
                0.0,
            )
            upsert_values.append(upsert_ms)
            search_values.append(search_ms)
            target_ranks.append(target_rank)
            target_scores.append(target_score)
            rows.append(
                {
                    "repeat": repeat,
                    "upsert_ms": round(upsert_ms, 2),
                    "search_ms": round(search_ms, 2),
                    "target_rank": target_rank,
                    "target_score": round(target_score, 4),
                }
            )
        finally:
            index.delete(reference["case_id"], peer_id=reference["peer_id"])
            index.delete(query["case_id"], peer_id=query["peer_id"])

    print(
        json.dumps(
            {
                "ready_ms": round(ready_ms, 2),
                "repeats": repeats,
                "upsert": summarize(upsert_values),
                "search": summarize(search_values),
                "target_rank_1_count": sum(rank == 1 for rank in target_ranks),
                "target_score": summarize_score(target_scores),
                "rows": rows,
                "temporary_cases_removed": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
