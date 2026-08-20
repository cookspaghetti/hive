from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hive.audit import JsonlAuditStore
from hive.audit_backup import create_audit_backup


def _ledger(path: Path) -> JsonlAuditStore:
    ledger = JsonlAuditStore(path)
    ledger.append_record(
        "message.received",
        "receive",
        component="test",
        payload={"text": "你好"},
        peer_id=123,
        session_id="session-1",
        level="info",
        ts=1.0,
    )
    return ledger


def test_create_audit_backup_preserves_and_verifies_chain(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    ledger = _ledger(source)

    result = create_audit_backup(source, tmp_path / "backups")

    backup = Path(result["path"])
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert JsonlAuditStore(backup).records() == ledger.records()
    assert result["events"] == 1
    assert result["source_id"] == ledger.source_id
    assert manifest["last_hash"] == ledger.records()[-1]["event_hash"]
    assert manifest["sha256"] == hashlib.sha256(backup.read_bytes()).hexdigest()


def test_create_audit_backup_uses_unique_immutable_names(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    _ledger(source)

    first = create_audit_backup(source, tmp_path / "backups")
    second = create_audit_backup(source, tmp_path / "backups")

    assert first["path"] != second["path"]
    assert Path(first["path"]).is_file()
    assert Path(second["path"]).is_file()


def test_create_audit_backup_rejects_tampered_source(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    _ledger(source)
    source.write_text(
        source.read_text(encoding="utf-8").replace("你好", "再见"),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="integrity"):
        create_audit_backup(source, tmp_path / "backups", attempts=1)

    assert not list((tmp_path / "backups").iterdir())
