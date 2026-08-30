"""Immutable, versioned intelligence analyses for archived takeover cases."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from hive.audit import audit_event
from hive.state import SessionState

ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_VERSIONS = {
    "extractor": "grounded-hvi-v1",
    "behavior_classifier": "message-evidence-v1",
    "verdict": "cumulative-evidence-v1",
    "report": "branded-unicode-v1",
}
_LEGACY_HISTORY_ID = re.compile(r"^[0-9]+_-?[0-9]+$")


def _uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


def _valid_history_id(value: str) -> bool:
    return bool(_LEGACY_HISTORY_ID.fullmatch(value) or _uuid4(value))


def transcript_sha256(messages: list[dict[str, Any]]) -> str:
    """Return a stable digest of the immutable transcript and media identity."""
    canonical = [
        {
            "role": str(message.get("role") or ""),
            "text": str(message.get("text") or ""),
            "ts": float(message.get("ts") or 0),
            "msg_id": int(message.get("msg_id") or 0),
            "media_sha256": str(message.get("media_sha256") or ""),
        }
        for message in messages
    ]
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def model_manifest(settings: Any | None) -> dict[str, str]:
    """Capture configured model identities without recording credentials."""
    if settings is None:
        return {}
    names = {
        "agent_cheap": "llm_model_cheap",
        "agent_strong": "llm_model_strong",
        "behavior_classifier": "llm_model_light",
        "vision": "vision_model",
    }
    return {
        label: str(value)
        for label, attribute in names.items()
        if (value := getattr(settings, attribute, ""))
    }


def original_analysis_metadata(
    history_id: str,
    messages: list[dict[str, Any]],
    *,
    created_ts: float,
    models: dict[str, str] | None = None,
    kind: str = "original",
) -> dict[str, Any]:
    """Create provenance for the analysis embedded in a sealed case record."""
    return {
        "id": str(uuid4()),
        "history_id": history_id,
        "kind": kind,
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "created_ts": created_ts,
        "transcript_sha256": transcript_sha256(messages),
        "models": dict(models or {}),
        "versions": dict(ANALYSIS_VERSIONS),
    }


def analysis_run_record(
    history_record: dict[str, Any],
    session: SessionState,
    *,
    models: dict[str, str] | None = None,
    created_ts: float | None = None,
) -> dict[str, Any]:
    """Build a standalone immutable reanalysis result for one archived case."""
    history_id = str(history_record.get("id") or "")
    if not _valid_history_id(history_id):
        raise ValueError("analysis run requires a valid history id")
    transcript = list(history_record.get("messages") or [])
    return {
        "id": str(uuid4()),
        "history_id": history_id,
        "peer_id": int(history_record["peer_id"]),
        "kind": "reanalysis",
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "created_ts": time.time() if created_ts is None else created_ts,
        "transcript_sha256": transcript_sha256(transcript),
        "models": dict(models or {}),
        "versions": dict(ANALYSIS_VERSIONS),
        "verdict": session.verdict,
        "score": round(session.verdict_score, 4),
        "turns": session.turn_count,
        "exchanges": session.exchange_count,
        "hvi_items": [
            {
                "kind": item.kind,
                "value": item.value,
                "confidence": round(item.confidence, 4),
                "source_msg_id": item.source_msg_id,
                "extractor": item.extractor,
            }
            for item in session.hvis
        ],
        "sandbox_results": list(session.sandbox_results),
        "threat_intelligence": list(session.threat_intelligence),
        "signal_trail": list(session.signal_trail),
        "media_analysis": list(session.media_analysis),
        "related_cases": list(session.related_cases),
    }


def analysis_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id",
            "history_id",
            "kind",
            "schema_version",
            "created_ts",
            "transcript_sha256",
            "models",
            "versions",
            "verdict",
            "score",
            "turns",
            "exchanges",
        )
    }


class AnalysisRunStore(Protocol):
    def create(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def list(self, history_id: str) -> list[dict[str, Any]]: ...

    def get(self, history_id: str, run_id: str) -> dict[str, Any] | None: ...


class LocalAnalysisRunStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        self._validate(record)
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record['id']}.json"
        if path.exists():
            raise ValueError("analysis run already exists")
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        self._audit(record, "analysis_run_persisted", "analysis.local")
        return record

    def list(self, history_id: str) -> list[dict[str, Any]]:
        if not _valid_history_id(history_id) or not self.root.is_dir():
            return []
        rows = [
            analysis_summary(record)
            for path in self.root.glob("*.json")
            if (record := self._read(path)) and record.get("history_id") == history_id
        ]
        return sorted(rows, key=lambda row: float(row.get("created_ts") or 0), reverse=True)

    def get(self, history_id: str, run_id: str) -> dict[str, Any] | None:
        if not _valid_history_id(history_id) or not _uuid4(run_id):
            return None
        record = self._read(self.root / f"{run_id}.json")
        return record if record and record.get("history_id") == history_id else None

    @staticmethod
    def _validate(record: dict[str, Any]) -> None:
        if not _uuid4(str(record.get("id") or "")):
            raise ValueError("analysis run must have a UUID4 id")
        if not _valid_history_id(str(record.get("history_id") or "")):
            raise ValueError("analysis run must reference a valid history id")

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _audit(record: dict[str, Any], action: str, component: str) -> None:
        audit_event(
            "analysis_run",
            action,
            component=component,
            payload={
                "analysis_run_id": record["id"],
                "history_id": record["history_id"],
                "schema_version": record.get("schema_version"),
                "transcript_sha256": record.get("transcript_sha256"),
            },
            peer_id=int(record["peer_id"]),
        )


class PostgresAnalysisRunStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._initialized = False

    def _connect(self) -> Any:
        import psycopg

        connection = psycopg.connect(self.database_url)
        if not self._initialized:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS takeover_analysis_runs (
                        id UUID PRIMARY KEY,
                        history_id TEXT NOT NULL,
                        peer_id BIGINT NOT NULL,
                        created_ts DOUBLE PRECISION NOT NULL,
                        record JSONB NOT NULL
                    )
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS takeover_analysis_runs_history_created_idx
                    ON takeover_analysis_runs (history_id, created_ts DESC)
                    """
                )
            connection.commit()
            self._initialized = True
        return connection

    def create(self, record: dict[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        LocalAnalysisRunStore._validate(record)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO takeover_analysis_runs (id, history_id, peer_id, created_ts, record)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    record["id"],
                    record["history_id"],
                    int(record["peer_id"]),
                    float(record["created_ts"]),
                    Jsonb(record),
                ),
            )
        LocalAnalysisRunStore._audit(record, "analysis_run_persisted", "analysis.postgres")
        return record

    def list(self, history_id: str) -> list[dict[str, Any]]:
        if not _valid_history_id(history_id):
            return []
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT record FROM takeover_analysis_runs
                WHERE history_id = %s ORDER BY created_ts DESC
                """,
                (history_id,),
            )
            return [analysis_summary(row[0]) for row in cursor.fetchall()]

    def get(self, history_id: str, run_id: str) -> dict[str, Any] | None:
        if not _valid_history_id(history_id) or not _uuid4(run_id):
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT record FROM takeover_analysis_runs
                WHERE history_id = %s AND id = %s
                """,
                (history_id, run_id),
            )
            row = cursor.fetchone()
            return row[0] if row else None


def build_analysis_run_store(
    root: str | Path,
    database_url: str = "",
) -> AnalysisRunStore:
    if database_url:
        return PostgresAnalysisRunStore(database_url)
    return LocalAnalysisRunStore(root)
