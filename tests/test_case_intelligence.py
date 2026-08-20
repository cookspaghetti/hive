"""Canonical case-profile and exact relationship tests."""

from hive.case_intelligence import (
    LocalCaseIntelligenceStore,
    build_case_profile,
    normalize_indicator,
)


def _history(case_id: str, account: str, *, phone: str = "") -> dict:
    hvi_items = [
        {
            "kind": "bank_account",
            "value": account,
            "confidence": 0.8,
            "source_msg_id": 2,
            "extractor": "regex",
        },
        {
            "kind": "person_name",
            "value": "John",
            "confidence": 0.9,
            "source_msg_id": 1,
            "extractor": "ner",
        },
    ]
    if phone:
        hvi_items.append(
            {
                "kind": "phone",
                "value": phone,
                "confidence": 0.8,
                "source_msg_id": 3,
                "extractor": "regex",
            }
        )
    return {
        "id": case_id,
        "peer_id": 919,
        "ended_ts": 20,
        "verdict": "likely_scam",
        "score": 0.9,
        "messages": [
            {"role": "stranger", "text": "invest now", "msg_id": 1, "ts": 10}
        ],
        "hvi_items": hvi_items,
        "signal_trail": [
            {
                "contributions": [
                    {
                        "reason": "soft:investment_framing",
                        "confidence": 0.8,
                    }
                ]
            }
        ],
        "sandbox_results": [],
        "analysis": {
            "id": "run-1",
            "schema_version": 1,
            "created_ts": 20,
            "transcript_sha256": "abc",
        },
    }


def test_case_profile_uses_only_valid_network_indicators():
    profile = build_case_profile(
        _history("8514213f-a1eb-4986-a2dc-bd3fa196ea96", "1234 5678")
    )

    assert profile["methods"][0]["key"] == "investment_framing"
    assert [(item["kind"], item["normalized_value"]) for item in profile["indicators"]] == [
        ("bank_account", "12345678")
    ]
    assert "person_name" not in profile["embedding_text"]


def test_case_profile_rejects_short_legacy_numeric_false_positives():
    profile = build_case_profile(
        _history("8514213f-a1eb-4986-a2dc-bd3fa196ea96", "123")
    )

    assert profile["indicators"] == []


def test_indicator_normalization_uses_domain_and_canonical_digits():
    assert normalize_indicator("url", "https://www.Example.com/pay?id=2") == "example.com"
    assert normalize_indicator("phone", "+60 12-345 6789") == "60123456789"


def test_local_store_links_exact_identifiers_but_not_names(tmp_path):
    store = LocalCaseIntelligenceStore(tmp_path)
    first = build_case_profile(
        _history("8514213f-a1eb-4986-a2dc-bd3fa196ea96", "12345678")
    )
    second = build_case_profile(
        _history("8d195b38-c1c3-4e00-ac1a-bcc5851ea2be", "12345678")
    )
    unrelated = build_case_profile(
        _history("96d7d19d-430b-4ba0-a31b-2d009fdbaa7c", "87654321")
    )

    store.index(first)
    store.index(second)
    store.index(unrelated)

    related = store.related(first["case_id"])
    assert len(related) == 1
    assert related[0]["related_case_id"] == second["case_id"]
    assert related[0]["relationship"] == "shared_identifier"
    assert related[0]["reasons"][0]["kind"] == "bank_account"
    assert related[0]["score"] == 0.95
