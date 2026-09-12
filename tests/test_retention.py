"""Report-only privacy and retention inventory tests."""

import os
from pathlib import Path

import pytest

from hive import retention
from hive.retention import RetentionPolicy, build_retention_report, validate_policy_payload


def _write_at(path: Path, modified: float, content: bytes = b"artifact") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (modified, modified))


def _category(report: dict, category_id: str) -> dict:
    return next(item for item in report["categories"] if item["id"] == category_id)


def test_report_flags_old_reviewable_files_without_deleting_them(tmp_path):
    now = 200 * 86400.0
    old_media = tmp_path / "evidence" / "media" / "old.jpg"
    recent_media = tmp_path / "evidence" / "media" / "recent.jpg"
    protected_bundle = tmp_path / "evidence" / "bundle_1_sample.pdf"
    _write_at(old_media, now - 31 * 86400)
    _write_at(recent_media, now - 2 * 86400)
    _write_at(protected_bundle, now - 150 * 86400)

    report = build_retention_report(tmp_path, RetentionPolicy(), now=now)

    media = _category(report, "captured_media")
    evidence = _category(report, "sealed_evidence")
    assert report["mode"] == "report_only"
    assert report["enforcement_available"] is False
    assert report["deletion_performed"] is False
    assert media["status"] == "review_due"
    assert media["review_due_count"] == 1
    assert evidence["status"] == "protected"
    assert evidence["review_due_count"] == 0
    assert old_media.exists() and recent_media.exists() and protected_bundle.exists()


def test_old_checkpoint_is_protected_but_flagged_for_operator_review(tmp_path):
    now = 50 * 86400.0
    checkpoint = tmp_path / "evidence" / "active_takeovers" / "123.json"
    _write_at(checkpoint, now - 8 * 86400)

    report = build_retention_report(tmp_path, RetentionPolicy(), now=now)
    active = _category(report, "active_takeovers")

    assert active["protected"] is True
    assert active["status"] == "protected"
    assert active["review_due_count"] == 1
    assert checkpoint.exists()


def test_report_merges_postgres_and_qdrant_inventory(tmp_path, monkeypatch):
    monkeypatch.setattr(
        retention,
        "_postgres_inventory",
        lambda _url: {
            "takeover_history": {"records": 4, "bytes": 500},
            "hive_audit_ledger": {"records": 7, "bytes": 900},
            "active_takeovers": {"records": 1, "bytes": 200},
        },
    )
    monkeypatch.setattr(
        retention,
        "_qdrant_inventory",
        lambda _url: {"records": 3, "bytes": 0},
    )

    report = build_retention_report(
        tmp_path,
        RetentionPolicy(),
        database_url="postgresql://inventory",
        qdrant_url="http://qdrant",
    )

    assert _category(report, "sealed_evidence")["record_count"] == 7
    assert _category(report, "audit_ledger")["record_count"] == 7
    assert _category(report, "active_takeovers")["record_count"] == 1
    assert report["summary"]["records"] == 15


@pytest.mark.parametrize("value", [0, -1, 3651, "not-a-number"])
def test_policy_rejects_invalid_review_thresholds(value):
    with pytest.raises(ValueError):
        validate_policy_payload({"media_days": value})


def test_policy_rejects_unknown_or_empty_settings():
    with pytest.raises(ValueError, match="at least one"):
        validate_policy_payload({})
    with pytest.raises(ValueError, match="unsupported"):
        validate_policy_payload({"delete_after_days": 30})
