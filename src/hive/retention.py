"""Non-destructive privacy and retention inventory for HIVE artifacts."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


@dataclass(frozen=True, slots=True)
class RetentionPolicy:
    """Operator-defined review thresholds for the report-only policy."""

    media_days: int = 30
    demo_days: int = 90
    evaluation_days: int = 180
    active_checkpoint_review_days: int = 7

    @classmethod
    def from_settings(cls, settings: Any) -> RetentionPolicy:
        return cls(
            media_days=_positive_days(getattr(settings, "retention_media_days", 30)),
            demo_days=_positive_days(getattr(settings, "retention_demo_days", 90)),
            evaluation_days=_positive_days(
                getattr(settings, "retention_evaluation_days", 180)
            ),
            active_checkpoint_review_days=_positive_days(
                getattr(settings, "retention_active_review_days", 7)
            ),
        )

    def to_dict(self) -> dict[str, int | str]:
        return {
            "mode": "report_only",
            "media_days": self.media_days,
            "demo_days": self.demo_days,
            "evaluation_days": self.evaluation_days,
            "active_checkpoint_review_days": self.active_checkpoint_review_days,
        }


def _positive_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("retention days must be an integer") from exc
    if days < 1 or days > 3650:
        raise ValueError("retention days must be between 1 and 3650")
    return days


def validate_policy_payload(payload: dict[str, Any]) -> dict[str, int]:
    allowed = {
        "media_days": "HIVE_RETENTION_MEDIA_DAYS",
        "demo_days": "HIVE_RETENTION_DEMO_DAYS",
        "evaluation_days": "HIVE_RETENTION_EVALUATION_DAYS",
        "active_checkpoint_review_days": "HIVE_RETENTION_ACTIVE_REVIEW_DAYS",
    }
    unknown = set(payload) - set(allowed)
    if unknown:
        raise ValueError(f"unsupported retention setting: {sorted(unknown)[0]}")
    if not payload:
        raise ValueError("at least one retention setting is required")
    return {allowed[key]: _positive_days(value) for key, value in payload.items()}


def _safe_files(root: Path, patterns: Iterable[str] = ("**/*",)) -> list[Path]:
    if not root.is_dir():
        return []
    resolved_root = root.resolve()
    files: dict[Path, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if (
                resolved.is_file()
                and (resolved == resolved_root or resolved_root in resolved.parents)
            ):
                files[resolved] = path
    return list(files.values())


def _file_category(
    category_id: str,
    label: str,
    detail: str,
    files: list[Path],
    *,
    now: float,
    retention_days: int | None,
    protected: bool,
) -> dict[str, Any]:
    stats = []
    for path in files:
        try:
            stat = path.stat()
        except OSError:
            continue
        stats.append((stat.st_mtime, stat.st_size))
    cutoff = now - retention_days * 86400 if retention_days is not None else None
    expired = [item for item in stats if cutoff is not None and item[0] < cutoff]
    if protected:
        status = "protected"
    elif expired:
        status = "review_due"
    elif stats:
        status = "within_policy"
    else:
        status = "empty"
    return {
        "id": category_id,
        "label": label,
        "detail": detail,
        "status": status,
        "protected": protected,
        "retention_days": retention_days,
        "file_count": len(stats),
        "record_count": 0,
        "bytes": sum(size for _modified, size in stats),
        "oldest_ts": min((modified for modified, _size in stats), default=None),
        "newest_ts": max((modified for modified, _size in stats), default=None),
        "review_due_count": len(expired),
        "review_due_bytes": sum(size for _modified, size in expired),
    }


def _postgres_inventory(database_url: str) -> dict[str, dict[str, int]]:
    if not database_url:
        return {}
    import psycopg
    from psycopg import sql

    tables = (
        "active_takeovers",
        "takeover_history",
        "takeover_analysis_runs",
        "hive_cases",
        "hive_case_indicators",
        "hive_case_relationships",
        "hive_audit_ledger",
    )
    inventory: dict[str, dict[str, int]] = {}
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        for table in tables:
            cursor.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            if cursor.fetchone()[0] is None:
                continue
            cursor.execute(
                sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))
            )
            count = int(cursor.fetchone()[0])
            cursor.execute("SELECT pg_total_relation_size(%s)", (f"public.{table}",))
            inventory[table] = {
                "records": count,
                "bytes": int(cursor.fetchone()[0] or 0),
            }
    return inventory


def _qdrant_inventory(qdrant_url: str) -> dict[str, int]:
    if not qdrant_url:
        return {}
    response = httpx.get(
        qdrant_url.rstrip("/") + "/collections/hive_cases",
        timeout=2.0,
    )
    if response.status_code == 404:
        return {"records": 0, "bytes": 0}
    response.raise_for_status()
    result = response.json().get("result") or {}
    return {
        "records": int(result.get("points_count") or 0),
        "bytes": 0,
    }


def _merge_records(
    category: dict[str, Any],
    *sources: dict[str, int] | None,
) -> None:
    for source in sources:
        if not source:
            continue
        category["record_count"] += int(source.get("records") or 0)
        category["bytes"] += int(source.get("bytes") or 0)


def build_retention_report(
    root: str | Path,
    policy: RetentionPolicy,
    *,
    database_url: str = "",
    qdrant_url: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Inventory artifacts and identify review candidates without deleting anything."""
    project_root = Path(root).resolve()
    generated = time.time() if now is None else now
    evidence_root = project_root / "evidence"
    categories = [
        _file_category(
            "sealed_evidence",
            "Sealed evidence and case records",
            "Signed bundles, packages, history, analysis runs, and case relationships.",
            [
                *_safe_files(
                    evidence_root,
                    ("bundle_*.pdf", "bundle_*.pdf.sig", "bundle_*.evidence.zip"),
                ),
                *_safe_files(evidence_root / "history"),
                *_safe_files(evidence_root / "cases"),
            ],
            now=generated,
            retention_days=None,
            protected=True,
        ),
        _file_category(
            "captured_media",
            "Captured Telegram media",
            "Original media and local analysis inputs linked to takeover evidence.",
            _safe_files(evidence_root / "media"),
            now=generated,
            retention_days=policy.media_days,
            protected=False,
        ),
        _file_category(
            "demo_runs",
            "Synthetic demo runs",
            "Demo transcripts, timelines, and synthetic evidence packages.",
            _safe_files(evidence_root / "demos"),
            now=generated,
            retention_days=policy.demo_days,
            protected=False,
        ),
        _file_category(
            "evaluation_results",
            "Evaluation results",
            "Red-team outputs, metrics, transcripts, and evaluation evidence.",
            _safe_files(project_root / "evaluation" / "results"),
            now=generated,
            retention_days=policy.evaluation_days,
            protected=False,
        ),
        _file_category(
            "audit_ledger",
            "Audit ledger and backups",
            "Hash-linked operational records retained to preserve audit continuity.",
            _safe_files(evidence_root / "audit"),
            now=generated,
            retention_days=None,
            protected=True,
        ),
        _file_category(
            "active_takeovers",
            "Unfinished takeover checkpoints",
            "Restart recovery state; old checkpoints require operator review, never expiry.",
            _safe_files(evidence_root / "active_takeovers"),
            now=generated,
            retention_days=policy.active_checkpoint_review_days,
            protected=True,
        ),
    ]
    warnings: list[str] = []
    try:
        postgres = _postgres_inventory(database_url)
    except Exception:  # noqa: BLE001 - report remains useful with partial inventory
        postgres = {}
        warnings.append("PostgreSQL inventory unavailable; file inventory is still complete.")
    try:
        qdrant = _qdrant_inventory(qdrant_url)
    except Exception:  # noqa: BLE001 - report remains useful with partial inventory
        qdrant = {}
        warnings.append("Qdrant inventory unavailable; other inventory is still complete.")

    by_id = {category["id"]: category for category in categories}
    _merge_records(
        by_id["sealed_evidence"],
        postgres.get("takeover_history"),
        postgres.get("takeover_analysis_runs"),
        postgres.get("hive_cases"),
        postgres.get("hive_case_indicators"),
        postgres.get("hive_case_relationships"),
        qdrant,
    )
    _merge_records(by_id["audit_ledger"], postgres.get("hive_audit_ledger"))
    _merge_records(by_id["active_takeovers"], postgres.get("active_takeovers"))
    review_due_count = sum(int(category["review_due_count"]) for category in categories)
    review_due_bytes = sum(int(category["review_due_bytes"]) for category in categories)
    return {
        "generated_ts": generated,
        "mode": "report_only",
        "enforcement_available": False,
        "deletion_performed": False,
        "policy": policy.to_dict(),
        "summary": {
            "categories": len(categories),
            "files": sum(int(category["file_count"]) for category in categories),
            "records": sum(int(category["record_count"]) for category in categories),
            "bytes": sum(int(category["bytes"]) for category in categories),
            "review_due_count": review_due_count,
            "review_due_bytes": review_due_bytes,
            "protected_categories": sum(
                bool(category["protected"]) for category in categories
            ),
        },
        "categories": categories,
        "warnings": warnings,
        "data_handling": {
            "telegram": "Private-chat text and approved media enter the local HIVE runtime.",
            "text_models": "Conversation text may be sent to the configured cloud text models.",
            "vision_models": (
                "Captured images use local QR/OCR first; configured vision fallback may "
                "receive image content."
            ),
            "qdrant": (
                "Only identifier-redacted scam-pattern profiles are embedded for "
                "candidate retrieval."
            ),
            "sealed_evidence": (
                "Exact transcripts and indicators remain in protected signed evidence "
                "and authoritative case records."
            ),
        },
    }
