"""Seed deterministic synthetic cases for the Case Intelligence interface.

Run inside the backend container so PostgreSQL and Qdrant use the deployed
configuration. The command replaces only the fixed case IDs declared below.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hive.analysis_runs import original_analysis_metadata
from hive.case_intelligence import build_case_intelligence_store, build_case_profile
from hive.config import load_settings
from hive.history import build_history_store

SEED_NAME = "hive_case_intelligence_mock_v1"


@dataclass(frozen=True)
class MockCase:
    case_id: str
    peer_id: int
    label: str
    verdict: str
    score: float
    stranger_messages: tuple[str, ...]
    methods: tuple[str, ...] = ()
    indicators: tuple[tuple[str, str, int], ...] = ()
    sandbox_results: tuple[dict[str, Any], ...] = ()


MOCK_CASES = (
    MockCase(
        case_id="c011ec7a-0001-4a11-8a11-000000000001",
        peer_id=99000101,
        label="Synthetic investment ring A",
        verdict="likely_scam",
        score=0.96,
        stranger_messages=(
            "Hey, I found a private Bitcoin investment group.",
            "Our analyst guarantees a 30 percent return this week.",
            "Transfer the activation fee to account 0000000001 now.",
            "If you wait, your slot will be gone.",
        ),
        methods=("investment_framing", "payment_request", "urgency"),
        indicators=(("bank_account", "0000000001", 5),),
    ),
    MockCase(
        case_id="c011ec7a-0002-4a11-8a11-000000000002",
        peer_id=99000102,
        label="Synthetic investment ring B — shared account",
        verdict="likely_scam",
        score=0.94,
        stranger_messages=(
            "Your crypto trading wallet is approved.",
            "The profit agent can unlock it after one activation payment.",
            "Send RM500 to bank account 0000000001.",
            "Complete it within ten minutes or the offer expires.",
        ),
        methods=("investment_framing", "payment_request", "urgency"),
        indicators=(("bank_account", "0000000001", 5),),
    ),
    MockCase(
        case_id="c011ec7a-0003-4a11-8a11-000000000003",
        peer_id=99000103,
        label="Synthetic investment candidate — different account",
        verdict="likely_scam",
        score=0.91,
        stranger_messages=(
            "Exclusive digital asset plan doubles deposits for new members.",
            "Pay the verification deposit to 0000000002.",
            "Do it today before registration closes.",
            "The trading manager will release your profit immediately.",
        ),
        methods=("investment_framing", "payment_request", "urgency"),
        indicators=(("bank_account", "0000000002", 3),),
    ),
    MockCase(
        case_id="c011ec7a-0004-4a11-8a11-000000000004",
        peer_id=99000104,
        label="Synthetic marketplace seller phishing A",
        verdict="likely_scam",
        score=0.93,
        stranger_messages=(
            "Hi, I already paid for your marketplace listing.",
            "Verify the seller account at https://seller-check-a.test/verify.",
            "Enter the banking password so the payment can be released.",
            "Please finish now because the order will be cancelled.",
        ),
        methods=("payment_request", "urgency"),
        indicators=(("url", "https://seller-check-a.test/verify", 3),),
        sandbox_results=(
            {
                "url": "https://seller-check-a.test/verify",
                "final_url": "https://seller-check-a.test/verify",
                "access_state": "reached",
                "verdict_signal": "credential_collection",
                "has_password_field": True,
                "challenge_detected": False,
                "status_code": 200,
                "fetcher": "synthetic_fixture",
            },
        ),
    ),
    MockCase(
        case_id="c011ec7a-0005-4a11-8a11-000000000005",
        peer_id=99000105,
        label="Synthetic marketplace seller phishing B",
        verdict="likely_scam",
        score=0.9,
        stranger_messages=(
            "Marketplace payment is waiting for seller confirmation.",
            "Open https://seller-check-b.test/release to receive your money.",
            "Confirm your online banking login before the buyer leaves.",
            "The transaction expires in fifteen minutes.",
        ),
        methods=("payment_request", "urgency"),
        indicators=(("url", "https://seller-check-b.test/release", 3),),
        sandbox_results=(
            {
                "url": "https://seller-check-b.test/release",
                "final_url": "https://seller-check-b.test/release",
                "access_state": "reached",
                "verdict_signal": "credential_collection",
                "has_password_field": True,
                "challenge_detected": False,
                "status_code": 200,
                "fetcher": "synthetic_fixture",
            },
        ),
    ),
    MockCase(
        case_id="c011ec7a-0006-4a11-8a11-000000000006",
        peer_id=99000106,
        label="Synthetic benign control",
        verdict="likely_benign",
        score=0.08,
        stranger_messages=(
            "Can we meet at the library tomorrow afternoon?",
            "I will bring the project notes.",
            "No rush, let me know which time works for you.",
        ),
    ),
)


def _message_rows(case: MockCase, started_ts: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    message_id = 1
    for index, text in enumerate(case.stranger_messages):
        rows.append(
            {
                "role": "stranger",
                "text": text,
                "ts": started_ts + index * 24,
                "msg_id": message_id,
                "media_kind": None,
                "media_name": None,
                "media_mime": None,
                "media_size": None,
                "media_available": False,
                "media_sha256": None,
                "captured_ts": started_ts + index * 24,
                "platform": "synthetic",
                "pre_takeover": False,
            }
        )
        message_id += 1
        if index < len(case.stranger_messages) - 1:
            rows.append(
                {
                    "role": "agent",
                    "text": "Could you explain that one detail at a time?",
                    "ts": started_ts + index * 24 + 8,
                    "msg_id": -message_id,
                    "media_kind": None,
                    "media_name": None,
                    "media_mime": None,
                    "media_size": None,
                    "media_available": False,
                    "media_sha256": None,
                    "captured_ts": started_ts + index * 24 + 8,
                    "platform": "synthetic",
                    "pre_takeover": False,
                }
            )
            message_id += 1
    return rows


def _record(case: MockCase, ended_ts: float) -> dict[str, Any]:
    started_ts = ended_ts - 180
    messages = _message_rows(case, started_ts)
    hvi_items = [
        {
            "kind": kind,
            "value": value,
            "confidence": 0.98,
            "source_msg_id": source_msg_id,
            "extractor": "synthetic_seed",
        }
        for kind, value, source_msg_id in case.indicators
    ]
    contributions = [
        {
            "reason": f"soft:{method}",
            "confidence": 0.94,
            "source_message_ids": [item[2] for item in case.indicators],
        }
        for method in case.methods
    ]
    analysis = original_analysis_metadata(
        case.case_id,
        messages,
        created_ts=ended_ts,
        models={"fixture": SEED_NAME},
    )
    return {
        "id": case.case_id,
        "session_id": case.case_id.replace("-", ""),
        "peer_id": case.peer_id,
        "peer_identity": {
            "display_name": case.label,
            "username": f"mock_hive_{case.peer_id}",
            "observed_ts": started_ts,
            "platform": "synthetic",
        },
        "persona": "confused_elderly",
        "phase": "sealed",
        "verdict": case.verdict,
        "score": case.score,
        "turns": len(case.stranger_messages),
        "exchanges": len(case.stranger_messages),
        "started_ts": started_ts,
        "ended_ts": ended_ts,
        "duration_s": 180,
        "messages": messages,
        "hvi_items": hvi_items,
        "sandbox_results": list(case.sandbox_results),
        "signal_trail": [
            {
                "name": "synthetic_behavior_assessment",
                "verdict": case.verdict,
                "score": case.score,
                "contributions": contributions,
            }
        ],
        "replay_of": None,
        "media_analysis": [],
        "related_cases": [],
        "analysis": analysis,
        "evidence_filename": None,
        "synthetic_seed": {"name": SEED_NAME, "label": case.label},
    }


def _stores():
    settings = load_settings()
    database_url = str(settings.database_url or "")
    if not database_url:
        raise RuntimeError("HIVE_DATABASE_URL is required for the deployed seed")
    history = build_history_store(Path("/app/evidence/history"), database_url)
    intelligence = build_case_intelligence_store(
        Path("/app/evidence/cases"),
        database_url,
        qdrant_url=str(settings.qdrant_url or ""),
        enable_semantic=bool(settings.use_case_similarity),
        similarity_threshold=float(settings.case_similarity_threshold),
        embedding_model=str(settings.case_embedding_model),
    )
    return settings, history, intelligence


def _remove_existing(settings: Any, intelligence: Any) -> int:
    case_ids = [case.case_id for case in MOCK_CASES]
    for case in MOCK_CASES:
        vectors = getattr(intelligence, "vectors", None)
        if vectors is not None:
            vectors.delete(case.case_id, peer_id=case.peer_id)

    import psycopg

    removed = 0
    with psycopg.connect(settings.database_url) as connection, connection.cursor() as cursor:
        for case_id in case_ids:
            cursor.execute(
                "DELETE FROM takeover_analysis_runs WHERE history_id = %s",
                (case_id,),
            )
            cursor.execute("DELETE FROM takeover_history WHERE id = %s", (case_id,))
            removed += cursor.rowcount
            cursor.execute("DELETE FROM hive_cases WHERE case_id = %s", (case_id,))
    return removed


def seed() -> dict[str, Any]:
    settings, history, intelligence = _stores()
    replaced = _remove_existing(settings, intelligence)
    base_time = time.time() + 1
    rows = []
    for index, case in enumerate(MOCK_CASES):
        record = _record(case, base_time - index * 60)
        history.import_record(record)
        profile = build_case_profile(record)
        intelligence.index(profile)
        rows.append(
            {
                "case_id": case.case_id,
                "peer_id": case.peer_id,
                "label": case.label,
                "verdict": case.verdict,
                "methods": profile["scam_vector"]["method_labels"],
                "indicators": profile["scam_vector"]["indicator_kinds"],
            }
        )
    return {
        "seed": SEED_NAME,
        "replaced_history_rows": replaced,
        "semantic_matching": bool(settings.use_case_similarity),
        "cases": rows,
    }


def remove() -> dict[str, Any]:
    settings, _history, intelligence = _stores()
    removed = _remove_existing(settings, intelligence)
    return {"seed": SEED_NAME, "removed_history_rows": removed}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Remove only this script's fixed synthetic cases.",
    )
    args = parser.parse_args()
    result = remove() if args.remove else seed()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
