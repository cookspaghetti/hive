"""Local, operator-only archive of completed takeover transcripts."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Protocol

from hive.logging_setup import get_logger
from hive.state import SessionState

log = get_logger(__name__)
_HISTORY_ID = re.compile(r"^[0-9]+_-?[0-9]+$")


class HistoryStore(Protocol):
    def archive(
        self,
        session: SessionState,
        *,
        evidence_path: str | Path | None = None,
        ended_ts: float | None = None,
        status: str = "sealed",
    ) -> dict[str, Any]: ...

    def list(self) -> list[dict[str, Any]]: ...

    def get(self, history_id: str) -> dict[str, Any] | None: ...


def _record(
    session: SessionState,
    *,
    evidence_path: str | Path | None,
    ended_ts: float | None,
    status: str,
) -> dict[str, Any]:
    ended = time.time() if ended_ts is None else ended_ts
    return {
        "id": f"{time.time_ns()}_{session.peer_id}",
        "peer_id": session.peer_id,
        "persona": session.persona,
        "phase": status,
        "verdict": session.verdict,
        "score": round(session.verdict_score, 3),
        "turns": session.turn_count,
        "started_ts": session.started_ts,
        "ended_ts": ended,
        "duration_s": max(0, round(ended - session.started_ts)) if session.started_ts else None,
        "messages": [
            {
                "role": message.role,
                "text": message.text,
                "ts": message.ts,
                "msg_id": message.msg_id,
                "media_kind": message.media_kind,
            }
            for message in session.messages
        ],
        "hvi_items": [
            {
                "kind": item.kind,
                "value": item.value,
                "confidence": round(item.confidence, 2),
            }
            for item in session.hvis
        ],
        "sandbox_results": session.sandbox_results,
        "signal_trail": session.signal_trail,
        "evidence_filename": Path(evidence_path).name if evidence_path else None,
    }


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "id",
            "peer_id",
            "persona",
            "phase",
            "verdict",
            "score",
            "turns",
            "started_ts",
            "ended_ts",
            "duration_s",
            "evidence_filename",
        )
    } | {"message_count": len(record.get("messages", []))}


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
    ) -> dict[str, Any]:
        record = _record(
            session,
            evidence_path=evidence_path,
            ended_ts=ended_ts,
            status=status,
        )
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{record['id']}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
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

    def get(self, history_id: str) -> dict[str, Any] | None:
        if not _HISTORY_ID.fullmatch(history_id):
            return None
        return self._read(self.root / f"{history_id}.json")

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
    ) -> dict[str, Any]:
        from psycopg.types.json import Jsonb

        record = _record(
            session,
            evidence_path=evidence_path,
            ended_ts=ended_ts,
            status=status,
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO takeover_history (id, peer_id, ended_ts, record)
                VALUES (%s, %s, %s, %s)
                """,
                (record["id"], record["peer_id"], record["ended_ts"], Jsonb(record)),
            )
        return record

    def list(self) -> list[dict[str, Any]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT record FROM takeover_history ORDER BY ended_ts DESC")
            return [_summary(row[0]) for row in cursor.fetchall()]

    def get(self, history_id: str) -> dict[str, Any] | None:
        if not _HISTORY_ID.fullmatch(history_id):
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT record FROM takeover_history WHERE id = %s", (history_id,))
            row = cursor.fetchone()
            return row[0] if row else None


def build_history_store(root: str | Path, database_url: str = "") -> HistoryStore:
    if database_url:
        return PostgresTakeoverHistoryStore(database_url)
    return TakeoverHistoryStore(root)
