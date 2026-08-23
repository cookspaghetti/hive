"""Verify an audit backup by restoring it into an isolated disposable target."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hive.audit import JsonlAuditStore

_HEX_32 = set("0123456789abcdef")
_ZERO_HASH = "0" * 64


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hex(value: Any, length: int, field: str) -> str:
    text = str(value)
    if len(text) != length or any(character not in _HEX_32 for character in text):
        raise ValueError(f"audit backup manifest has invalid {field}")
    return text


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"audit backup manifest not found: {manifest_path}")
    if manifest_path.stat().st_size > 64 * 1024:
        raise ValueError("audit backup manifest is unexpectedly large")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("audit backup manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("unsupported audit backup manifest schema")

    backup_name = manifest.get("backup_file")
    if not isinstance(backup_name, str) or Path(backup_name).name != backup_name:
        raise ValueError("audit backup manifest contains an unsafe backup filename")
    if not backup_name.endswith(".jsonl"):
        raise ValueError("audit backup manifest must reference a JSONL file")
    events = manifest.get("events")
    if isinstance(events, bool) or not isinstance(events, int) or events < 0:
        raise ValueError("audit backup manifest has an invalid event count")
    _hex(manifest.get("source_id"), 32, "source identity")
    _hex(manifest.get("last_hash"), 64, "last hash")
    _hex(manifest.get("sha256"), 64, "SHA-256 checksum")
    return manifest


def latest_audit_manifest(backup_root: str | Path) -> Path:
    root = Path(backup_root)
    manifests = sorted(
        root.glob("events_*.manifest.json"),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    if not manifests:
        raise FileNotFoundError(f"no audit backup manifests found in {root}")
    return manifests[0]


def _write_report(result: dict[str, Any], report_root: Path) -> Path:
    report_root.mkdir(parents=True, exist_ok=True)
    created = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = report_root / f"audit_restore_{created}_{uuid.uuid4().hex[:8]}.json"
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp_path, path)
    return path


def _clean_disposable_restore(target: Path, restored: Path) -> None:
    resolved_target = target.resolve()
    resolved_restored = restored.resolve()
    if resolved_restored.parent != resolved_target or not target.name.startswith(
        "hive-audit-restore-"
    ):
        raise RuntimeError("refusing to clean an unexpected restore target")
    restored.unlink(missing_ok=True)
    restored.with_suffix(restored.suffix + ".source").unlink(missing_ok=True)
    target.rmdir()


def run_audit_restore_drill(
    manifest_path: str | Path,
    *,
    work_root: str | Path | None = None,
    report_root: str | Path | None = None,
    keep_restored: bool = False,
) -> dict[str, Any]:
    """Restore one backup into a new target and verify it without touching source data."""
    started = time.perf_counter()
    manifest_file = Path(manifest_path).resolve()
    manifest = _load_manifest(manifest_file)
    backup_root = manifest_file.parent.resolve()
    backup = (backup_root / manifest["backup_file"]).resolve()
    if backup.parent != backup_root or not backup.is_file():
        raise ValueError("audit backup file is missing or outside the backup directory")
    source_identity = backup.with_suffix(backup.suffix + ".source")
    if not source_identity.is_file():
        raise ValueError("audit backup source identity sidecar is missing")

    checks = {
        "manifest_schema": True,
        "safe_backup_path": True,
        "backup_checksum": _sha256(backup) == manifest["sha256"],
        "source_identity": source_identity.read_text(encoding="ascii").strip()
        == manifest["source_id"],
    }
    if not checks["backup_checksum"]:
        raise RuntimeError("audit backup checksum does not match its manifest")
    if not checks["source_identity"]:
        raise RuntimeError("audit backup source identity does not match its manifest")

    workspace = Path(work_root).resolve() if work_root else None
    if workspace is not None:
        workspace.mkdir(parents=True, exist_ok=True)
    target = Path(
        tempfile.mkdtemp(
            prefix="hive-audit-restore-",
            dir=str(workspace) if workspace is not None else None,
        )
    ).resolve()
    restored = target / backup.name
    cleaned = False
    try:
        shutil.copyfile(backup, restored)
        shutil.copyfile(source_identity, restored.with_suffix(restored.suffix + ".source"))
        ledger = JsonlAuditStore(restored)
        rows = ledger.records()
        checks.update(
            {
                "hash_chain": ledger.verify(),
                "event_count": len(rows) == manifest["events"],
                "restored_source_identity": ledger.source_id == manifest["source_id"],
                "last_hash": (rows[-1]["event_hash"] if rows else _ZERO_HASH)
                == manifest["last_hash"],
            }
        )
        if not all(checks.values()):
            failed = next(name for name, passed in checks.items() if not passed)
            raise RuntimeError(f"audit restore verification failed: {failed}")
        result: dict[str, Any] = {
            "schema_version": 1,
            "status": "passed",
            "created_at": datetime.now(UTC).isoformat(),
            "duration_seconds": round(time.perf_counter() - started, 6),
            "manifest_file": manifest_file.name,
            "manifest_sha256": _sha256(manifest_file),
            "backup_file": backup.name,
            "backup_sha256": manifest["sha256"],
            "source_id": manifest["source_id"],
            "events": len(rows),
            "last_hash": manifest["last_hash"],
            "checks": checks,
            "source_modified": False,
            "live_stores_modified": False,
            "restore_mode": "disposable_isolated_target",
            "restored_path": str(restored) if keep_restored else None,
            "cleaned": not keep_restored,
        }
    finally:
        if not keep_restored:
            _clean_disposable_restore(target, restored)
            cleaned = True

    result["cleaned"] = cleaned
    if report_root is not None:
        report_path = _write_report(result, Path(report_root))
        result["report"] = str(report_path)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", help="Backup manifest; defaults to the newest backup")
    parser.add_argument("--backup-root", default="./evidence/audit/backups")
    parser.add_argument("--work-root", help="Parent for the new disposable restore directory")
    parser.add_argument(
        "--report-root",
        default="./evaluation/results/backup_restore",
        help="Directory that receives the drill result JSON",
    )
    parser.add_argument(
        "--keep-restored",
        action="store_true",
        help="Keep the isolated restored copy for manual inspection",
    )
    args = parser.parse_args()
    manifest = Path(args.manifest) if args.manifest else latest_audit_manifest(args.backup_root)
    result = run_audit_restore_drill(
        manifest,
        work_root=args.work_root,
        report_root=args.report_root,
        keep_restored=args.keep_restored,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
