"""Create verified, immutable snapshots of HIVE's local audit ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.audit import JsonlAuditStore
from hive.config import Settings


def _encode_rows(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        for row in rows
    )


def create_audit_backup(
    source_path: str | Path,
    backup_root: str | Path,
    *,
    attempts: int = 3,
) -> dict[str, Any]:
    """Snapshot and verify a hash-chained ledger without changing the source."""
    source = Path(source_path)
    destination = Path(backup_root)
    destination.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            ledger = JsonlAuditStore(source)
            rows = ledger.records()
            if not ledger.verify():
                raise RuntimeError(f"audit ledger integrity check failed: {source}")
            break
        except (OSError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 >= max(1, attempts):
                raise
            time.sleep(0.05)
    else:  # pragma: no cover - loop always breaks or raises
        raise RuntimeError("could not read audit ledger") from last_error

    created = datetime.now(UTC)
    suffix = uuid.uuid4().hex[:8]
    name = f"events_{created.strftime('%Y%m%dT%H%M%SZ')}_{suffix}.jsonl"
    backup_path = destination / name
    temp_path = destination / f".{name}.tmp"
    source_id_path = backup_path.with_suffix(backup_path.suffix + ".source")
    temp_source_id_path = temp_path.with_suffix(temp_path.suffix + ".source")
    payload = _encode_rows(rows)

    try:
        temp_path.write_bytes(payload)
        temp_source_id_path.write_text(ledger.source_id, encoding="ascii")
        os.replace(temp_path, backup_path)
        os.replace(temp_source_id_path, source_id_path)
        verified = JsonlAuditStore(backup_path)
        if not verified.verify() or verified.records() != rows:
            raise RuntimeError(f"audit backup verification failed: {backup_path}")
    except Exception:
        temp_path.unlink(missing_ok=True)
        temp_source_id_path.unlink(missing_ok=True)
        backup_path.unlink(missing_ok=True)
        source_id_path.unlink(missing_ok=True)
        raise

    manifest = {
        "schema_version": 1,
        "created_at": created.isoformat(),
        "source_path": str(source.resolve()),
        "source_id": ledger.source_id,
        "events": len(rows),
        "last_hash": rows[-1]["event_hash"] if rows else "0" * 64,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "backup_file": backup_path.name,
    }
    manifest_path = backup_path.with_suffix(".manifest.json")
    temp_manifest_path = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temp_manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_manifest_path, manifest_path)
    return {**manifest, "path": str(backup_path), "manifest": str(manifest_path)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        help="Ledger JSONL path (defaults to HIVE_AUDIT_PATH)",
    )
    parser.add_argument(
        "--destination",
        default="./evidence/audit/backups",
        help="Directory that receives verified snapshots",
    )
    args = parser.parse_args()
    settings = Settings()
    result = create_audit_backup(args.source or settings.audit_path, args.destination)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
