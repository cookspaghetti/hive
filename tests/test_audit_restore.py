"""Disposable audit-backup restore drill tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hive.audit import JsonlAuditStore
from hive.audit_backup import create_audit_backup
from hive.audit_restore import latest_audit_manifest, run_audit_restore_drill


def _backup(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "events.jsonl"
    ledger = JsonlAuditStore(source)
    ledger.append_record(
        "operator_event",
        "test_event",
        component="test.restore",
        payload={"value": "synthetic"},
        peer_id=None,
        session_id=None,
        level="info",
        ts=1.0,
    )
    created = create_audit_backup(source, tmp_path / "backups")
    return source, Path(created["manifest"])


def test_restore_drill_verifies_and_cleans_isolated_target(tmp_path: Path) -> None:
    source, manifest = _backup(tmp_path)
    original = source.read_bytes()

    result = run_audit_restore_drill(
        manifest,
        work_root=tmp_path / "work",
        report_root=tmp_path / "reports",
    )

    assert result["status"] == "passed"
    assert result["events"] == 1
    assert all(result["checks"].values())
    assert result["source_modified"] is False
    assert result["live_stores_modified"] is False
    assert result["restored_path"] is None
    assert result["cleaned"] is True
    assert source.read_bytes() == original
    assert not list((tmp_path / "work").iterdir())
    report = json.loads(Path(result["report"]).read_text(encoding="utf-8"))
    assert report["status"] == "passed"
    assert "report" not in report


def test_restore_drill_can_keep_copy_for_manual_inspection(tmp_path: Path) -> None:
    _source, manifest = _backup(tmp_path)

    result = run_audit_restore_drill(
        manifest,
        work_root=tmp_path / "work",
        keep_restored=True,
    )

    restored = Path(result["restored_path"])
    assert restored.is_file()
    assert JsonlAuditStore(restored).status()["events"] == 1
    assert result["cleaned"] is False


def test_restore_drill_rejects_tampered_backup_before_restore(tmp_path: Path) -> None:
    _source, manifest = _backup(tmp_path)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    backup = manifest.parent / metadata["backup_file"]
    backup.write_bytes(backup.read_bytes() + b"tampered")

    with pytest.raises(RuntimeError, match="checksum"):
        run_audit_restore_drill(manifest, work_root=tmp_path / "work")

    assert not (tmp_path / "work").exists()


def test_restore_drill_rejects_unsafe_manifest_path(tmp_path: Path) -> None:
    _source, manifest = _backup(tmp_path)
    metadata = json.loads(manifest.read_text(encoding="utf-8"))
    metadata["backup_file"] = "../events.jsonl"
    manifest.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe"):
        run_audit_restore_drill(manifest)


def test_latest_manifest_selects_most_recent_file(tmp_path: Path) -> None:
    root = tmp_path / "backups"
    root.mkdir()
    older = root / "events_older.manifest.json"
    newer = root / "events_newer.manifest.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    older.touch()
    newer.touch()
    older_time = older.stat().st_mtime - 10
    import os

    os.utime(older, (older_time, older_time))

    assert latest_audit_manifest(root) == newer

