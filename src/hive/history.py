"""Local, operator-only archive of completed takeover transcripts."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from hive.analysis_runs import original_analysis_metadata
from hive.audit import audit_event
from hive.logging_setup import get_logger
from hive.state import SessionState

log = get_logger(__name__)
_HISTORY_ID = re.compile(r"^[0-9]+_-?[0-9]+$")


def _valid_history_id(value: str) -> bool:
    if _HISTORY_ID.fullmatch(value):
        return True
    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value.lower()


class HistoryStore(Protocol):
    def archive(
        self,
        session: SessionState,
        *,
        evidence_path: str | Path | None = None,
        ended_ts: float | None = None,
        status: str = "sealed",
        analysis_models: dict[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def list(self) -> list[dict[str, Any]]: ...

    def get(self, history_id: str) -> dict[str, Any] | None: ...

    def import_record(self, record: dict[str, Any]) -> dict[str, Any]: ...


def _record(
    session: SessionState,
    *,
    evidence_path: str | Path | None,
    ended_ts: float | None,
    status: str,
    analysis_models: dict[str, str] | None,
) -> dict[str, Any]:
    ended = time.time() if ended_ts is None else ended_ts
    history_id = str(uuid4())
    messages = [
        {
            "role": message.role,
            "text": message.text,
            "ts": message.ts,
            "msg_id": message.msg_id,
            "media_kind": message.media_kind,
            "media_name": message.media_name,
            "media_mime": message.media_mime,
            "media_size": message.media_size,
            "media_available": bool(message.media_path),
            "media_sha256": message.media_sha256,
        }
        for message in session.messages
    ]
    analysis = original_analysis_metadata(
        history_id,
        messages,
        created_ts=ended,
        models=analysis_models,
        kind="reanalysis" if session.replay_of else "original",
    )
    return {
        "id": history_id,
        "session_id": session.session_id,
        "peer_id": session.peer_id,
        "persona": session.persona,
        "phase": status,
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "exchanges": getattr(session, "exchange_count", session.turn_count),
        "started_ts": session.started_ts,
        "ended_ts": ended,
        "duration_s": max(0, round(ended - session.started_ts)) if session.started_ts else None,
        "messages": messages,
        "hvi_items": [
            {
                "kind": item.kind,
                "value": item.value,
                "confidence": round(item.confidence, 2),
                "source_msg_id": item.source_msg_id,
                "extractor": item.extractor,
            }
            for item in session.hvis
        ],
        "sandbox_results": session.sandbox_results,
        "signal_trail": session.signal_trail,
        "replay_of": session.replay_of,
        "media_analysis": session.media_analysis,
        "analysis": analysis,
        "evidence_filename": Path(evidence_path).name if evidence_path else None,
    }


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id",
            "session_id",
            "peer_id",
            "persona",
            "phase",
            "verdict",
            "score",
            "turns",
            "exchanges",
            "started_ts",
            "ended_ts",
            "duration_s",
            "evidence_filename",
        )
    } | {
        "message_count": len(record.get("messages", [])),
        "analysis_run_id": (record.get("analysis") or {}).get("id"),
        "analysis_schema_version": (record.get("analysis") or {}).get("schema_version"),
    }


class TakeoverHistoryStore:
    """Persist one JSON record per completed takeover."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def archive(
        self,
        session: SessionState,
        *,
        evidence_path: str | Path | None = None,
        ended_ts: float | None = None,
        status: str = "sealed",
        analysis_models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        record = _record(
            session,
            evidence_path=evidence_path,
            ended_ts=ended_ts,
            status=status,
            analysis_models=analysis_models,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        audit_event(
            "takeover_history",
            "takeover_history_archived",
            component="history.local",
            payload={"record": record, "path": str(path)},
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return record

    def list(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return rows
        for path in self.root.glob("*.json"):
            record = self._read(path)
            if record is None:
                continue
            rows.append(_summary(record))
        return sorted(rows, key=lambda row: float(row.get("ended_ts") or 0), reverse=True)

    def import_record(self, record: dict[str, Any]) -> dict[str, Any]:
        history_id = str(record.get("id") or "")
        if not _valid_history_id(history_id):
            raise ValueError("history record must have a valid legacy or UUID4 id")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{history_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        return record

    def get(self, history_id: str) -> dict[str, Any] | None:
        if not _valid_history_id(history_id):
            return None
        direct = self._read(self.root / f"{history_id}.json")
        if direct is not None or not _HISTORY_ID.fullmatch(history_id):
            return direct
        for path in self.root.glob("*.json"):
            record = self._read(path)
            if record and record.get("legacy_id") == history_id:
                return record
        return None

    def migrate_legacy_ids(self) -> dict[str, str]:
        """Replace timestamp-based IDs with UUID4 IDs while retaining aliases."""
        migrated: dict[str, str] = {}
        if not self.root.is_dir():
            return migrated
        for path in list(self.root.glob("*.json")):
            record = self._read(path)
            old_id = str(record.get("id") or "") if record else ""
            if record is None or not _HISTORY_ID.fullmatch(old_id):
                continue
            new_id = str(uuid4())
            record["legacy_id"] = old_id
            record["id"] = new_id
            target = self.root / f"{new_id}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(record, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary.replace(target)
            path.unlink()
            migrated[old_id] = new_id
        if migrated:
            audit_event(
                "takeover_history",
                "legacy_history_ids_migrated",
                component="history.local",
                payload={"mapping": migrated},
            )
        return migrated

    @staticmethod
    def _read(path: Path) -> dict[str, Any] | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("takeover history: ignoring unreadable record %s", path)
            return None
        return value if isinstance(value, dict) else None


class PostgresTakeoverHistoryStore:
    """PostgreSQL-backed history store used by the Compose deployment."""

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
                    CREATE TABLE IF NOT EXISTS takeover_history (
                        id TEXT PRIMARY KEY,
                        peer_id BIGINT NOT NULL,
                        ended_ts DOUBLE PRECISION NOT NULL,
                        record JSONB NOT NULL
                    )
                    """
                )
            connection.commit()
            self._initialized = True
        return connection

    def archive(
        self,
        session: SessionState,
        *,
        evidence_path: str | Path | None = None,
        ended_ts: float | None = None,
        status: str = "sealed",
        analysis_models: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        record = _record(
            session,
            evidence_path=evidence_path,
            ended_ts=ended_ts,
            status=status,
            analysis_models=analysis_models,
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO takeover_history (id, peer_id, ended_ts, record)
                VALUES (%s, %s, %s, %s)
                """,
                (record["id"], record["peer_id"], record["ended_ts"], Jsonb(record)),
            )
        audit_event(
            "takeover_history",
            "takeover_history_archived",
            component="history.postgres",
            payload={"record": record},
            peer_id=session.peer_id,
            session_id=session.session_id,
        )
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT record FROM takeover_history ORDER BY ended_ts DESC")
            return [_summary(row[0]) for row in cursor.fetchall()]

    def import_record(self, record: dict[str, Any]) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        history_id = str(record.get("id") or "")
        if not _valid_history_id(history_id):
            raise ValueError("history record must have a valid legacy or UUID4 id")
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO takeover_history (id, peer_id, ended_ts, record)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET record = EXCLUDED.record
                """,
                (
                    history_id,
                    int(record["peer_id"]),
                    float(record["ended_ts"]),
                    Jsonb(record),
                ),
            )
        return record

    def get(self, history_id: str) -> dict[str, Any] | None:
        if not _valid_history_id(history_id):
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT record FROM takeover_history WHERE id = %s", (history_id,))
            row = cursor.fetchone()
            if row is None and _HISTORY_ID.fullmatch(history_id):
                cursor.execute(
                    "SELECT record FROM takeover_history WHERE record->>'legacy_id' = %s",
                    (history_id,),
                )
                row = cursor.fetchone()
            return row[0] if row else None

    def migrate_legacy_ids(self) -> dict[str, str]:
        """Replace timestamp-based IDs with UUID4 IDs while retaining aliases."""
        from psycopg.types.json import Jsonb

        migrated: dict[str, str] = {}
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, record FROM takeover_history")
            for old_id, record in cursor.fetchall():
                if not _HISTORY_ID.fullmatch(str(old_id)):
                    continue
                new_id = str(uuid4())
                record = dict(record)
                record["legacy_id"] = str(old_id)
                record["id"] = new_id
                cursor.execute(
                    "UPDATE takeover_history SET id = %s, record = %s WHERE id = %s",
                    (new_id, Jsonb(record), old_id),
                )
                migrated[str(old_id)] = new_id
        if migrated:
            audit_event(
                "takeover_history",
                "legacy_history_ids_migrated",
                component="history.postgres",
                payload={"mapping": migrated},
            )
        return migrated


def build_history_store(root: str | Path, database_url: str = "") -> HistoryStore:
    if database_url:
        return PostgresTakeoverHistoryStore(database_url)
    return TakeoverHistoryStore(root)
