"""Permanent, append-only audit ledger for all HIVE operational activity.

The UTF-8 JSONL ledger is the durable source of truth. Every record includes
the previous record hash, making deletion, reordering, and modification
detectable. A PostgreSQL mirror is maintained when configured; local writes
remain authoritative so a temporary database outage cannot create an audit
gap.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Protocol, cast

_ZERO_HASH = "0" * 64
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+"),
    re.compile(r"(?i)((?:api[_ -]?key|token|password|passphrase)\s*[:=]\s*)[^\s,;]+"),
    re.compile(r"\b\d{5,}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:sk|hf)_[A-Za-z0-9_-]{12,}\b"),
)
_MIRROR_RETRY_SECONDS = 30.0


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _hash_record(record: dict[str, Any]) -> str:
    body = {key: value for key, value in record.items() if key != "event_hash"}
    return hashlib.sha256(_json(body).encode("utf-8")).hexdigest()


def _json_value(value: object) -> Any:
    """Return the exact JSON-safe representation used by both durable stores."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _redact_log_message(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(
            lambda match: f"{match.group(1)}[redacted]" if match.lastindex else "[redacted]",
            text,
        )
    return text


class AuditLedger(Protocol):
    def append(
        self,
        event_type: str,
        action: str,
        *,
        component: str,
        payload: dict[str, Any] | None = None,
        peer_id: int | None = None,
        session_id: str | None = None,
        level: str = "info",
        ts: float | None = None,
    ) -> dict[str, Any]: ...

    def list(
        self,
        *,
        after: int = 0,
        limit: int = 200,
        peer_id: int | None = None,
        event_type: str = "",
        event_types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]: ...

    def status(self) -> dict[str, Any]: ...


class NullAuditLedger:
    def append(
        self,
        event_type: str,
        action: str,
        *,
        component: str,
        payload: dict[str, Any] | None = None,
        peer_id: int | None = None,
        session_id: str | None = None,
        level: str = "info",
        ts: float | None = None,
    ) -> dict[str, Any]:
        return {}

    def list(
        self,
        *,
        after: int = 0,
        limit: int = 200,
        peer_id: int | None = None,
        event_type: str = "",
        event_types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        return []

    def status(self) -> dict[str, Any]:
        return {"enabled": False, "valid": True, "events": 0}


class JsonlAuditStore:
    """Synchronous hash-chained JSONL store; each append is flushed to disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.source_id = self._load_source_id()
        self._sequence = 0
        self._last_hash = _ZERO_HASH
        self._known_stat: tuple[int, int] | None = None
        self._load_and_verify()

    def _load_source_id(self) -> str:
        path = self.path.with_suffix(self.path.suffix + ".source")
        if path.is_file():
            value = path.read_text(encoding="ascii").strip()
            if value:
                return value
        value = ""
        if self.path.is_file():
            with self.path.open("r", encoding="utf-8") as stream:
                first_line = next((line for line in stream if line.strip()), "")
            if first_line:
                try:
                    value = str(json.loads(first_line).get("source_id", ""))
                except (json.JSONDecodeError, AttributeError):
                    pass
        value = value or uuid.uuid4().hex
        path.write_text(value, encoding="ascii")
        return value

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"audit ledger contains invalid JSON at line {line_number}"
                    ) from exc
                if not isinstance(value, dict):
                    raise RuntimeError(f"audit ledger line {line_number} is not an object")
                rows.append(value)
        return rows

    @staticmethod
    def _verify_rows(rows: list[dict[str, Any]]) -> bool:
        previous = _ZERO_HASH
        expected_sequence = 1
        for record in rows:
            if record.get("source_id") != rows[0].get("source_id"):
                return False
            if record.get("sequence") != expected_sequence:
                return False
            if record.get("prev_hash") != previous:
                return False
            if record.get("event_hash") != _hash_record(record):
                return False
            previous = str(record["event_hash"])
            expected_sequence += 1
        return True

    def _load_and_verify(self) -> None:
        rows = self._read_all()
        if not self._verify_rows(rows):
            raise RuntimeError(f"audit ledger integrity check failed: {self.path}")
        if rows and rows[0].get("source_id") != self.source_id:
            raise RuntimeError(f"audit ledger source identity check failed: {self.path}")
        if rows:
            self._sequence = int(rows[-1]["sequence"])
            self._last_hash = str(rows[-1]["event_hash"])
        self._known_stat = self._stat()

    def _stat(self) -> tuple[int, int] | None:
        try:
            value = self.path.stat()
        except FileNotFoundError:
            return None
        return value.st_size, value.st_mtime_ns

    def append_record(
        self,
        event_type: str,
        action: str,
        *,
        component: str,
        payload: dict[str, Any] | None,
        peer_id: int | None,
        session_id: str | None,
        level: str,
        ts: float | None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._stat() != self._known_stat:
                raise RuntimeError(
                    f"audit ledger changed outside the active process: {self.path}"
                )
            record = {
                "event_id": uuid.uuid4().hex,
                "source_id": self.source_id,
                "sequence": self._sequence + 1,
                "ts": time.time() if ts is None else ts,
                "event_type": str(event_type),
                "component": str(component),
                "action": str(action),
                "level": str(level),
                "peer_id": peer_id,
                "session_id": session_id,
                "payload": _json_value(payload or {}),
                "prev_hash": self._last_hash,
            }
            record["event_hash"] = _hash_record(record)
            encoded = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            self._sequence = int(record["sequence"])
            self._last_hash = str(record["event_hash"])
            self._known_stat = self._stat()
            return record

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._read_all()

    def verify(self) -> bool:
        with self._lock:
            if self._stat() == self._known_stat:
                return True
            return self._verify_rows(self._read_all())

    def status(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "source_id": self.source_id,
            "events": self._sequence,
            "last_hash": self._last_hash,
            "valid": self.verify(),
        }


class PostgresAuditMirror:
    """Query-friendly PostgreSQL mirror of authoritative JSONL records."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._initialized = False
        self._lock = threading.Lock()

    def _connect(self) -> Any:
        import psycopg

        connection = psycopg.connect(self.database_url)
        if not self._initialized:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS hive_audit_ledger (
                        event_id TEXT PRIMARY KEY,
                        source_id TEXT NOT NULL,
                        sequence BIGINT NOT NULL,
                        ts DOUBLE PRECISION NOT NULL,
                        event_type TEXT NOT NULL,
                        component TEXT NOT NULL,
                        action TEXT NOT NULL,
                        level TEXT NOT NULL,
                        peer_id BIGINT,
                        session_id TEXT,
                        payload JSONB NOT NULL,
                        prev_hash TEXT NOT NULL,
                        event_hash TEXT NOT NULL,
                        UNIQUE (source_id, sequence)
                    )
                    """
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS hive_audit_peer_ts "
                    "ON hive_audit_ledger (peer_id, ts DESC)"
                )
                cursor.execute(
                    "CREATE INDEX IF NOT EXISTS hive_audit_type_ts "
                    "ON hive_audit_ledger (event_type, ts DESC)"
                )
            connection.commit()
            self._initialized = True
        return connection

    @staticmethod
    def _write(cursor: Any, record: dict[str, Any]) -> None:
        from psycopg.types.json import Jsonb

        cursor.execute(
            """
            INSERT INTO hive_audit_ledger (
                event_id, source_id, sequence, ts, event_type, component,
                action, level, peer_id, session_id, payload, prev_hash, event_hash
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id) DO NOTHING
            """,
            (
                record["event_id"],
                record["source_id"],
                record["sequence"],
                record["ts"],
                record["event_type"],
                record["component"],
                record["action"],
                record["level"],
                record["peer_id"],
                record["session_id"],
                Jsonb(record["payload"]),
                record["prev_hash"],
                record["event_hash"],
            ),
        )

    def write(self, record: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection, connection.cursor() as cursor:
            self._write(cursor, record)

    def sync(self, records: Iterable[dict[str, Any]]) -> None:
        with self._lock, self._connect() as connection, connection.cursor() as cursor:
            for record in records:
                self._write(cursor, record)

    def list(
        self,
        *,
        source_id: str,
        after: int = 0,
        limit: int = 200,
        peer_id: int | None = None,
        event_type: str = "",
        event_types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read a bounded, source-scoped window from the query mirror."""
        columns = (
            "event_id",
            "source_id",
            "sequence",
            "ts",
            "event_type",
            "component",
            "action",
            "level",
            "peer_id",
            "session_id",
            "payload",
            "prev_hash",
            "event_hash",
        )
        clauses = ["source_id = %s", "sequence > %s"]
        parameters: list[Any] = [source_id, after]
        if peer_id is not None:
            clauses.append("peer_id = %s")
            parameters.append(peer_id)
        if event_type:
            clauses.append("event_type = %s")
            parameters.append(event_type)
        if event_types is not None:
            selected_types = sorted(set(event_types))
            if not selected_types:
                return []
            clauses.append("event_type = ANY(%s)")
            parameters.append(selected_types)
        parameters.append(max(1, min(limit, 1000)))
        query = f"""
            SELECT {", ".join(columns)}
            FROM hive_audit_ledger
            WHERE {" AND ".join(clauses)}
            ORDER BY sequence DESC
            LIMIT %s
        """
        with self._lock, self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(query, parameters)
            rows: list[dict[str, Any]] = [
                dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
            ]
        rows.reverse()
        return rows


class DurableAuditLedger:
    def __init__(self, path: str | Path, database_url: str = "") -> None:
        self.local = JsonlAuditStore(path)
        self.mirror = PostgresAuditMirror(database_url) if database_url else None
        self._mirror_dirty = False
        self._mirror_error = ""
        self._next_mirror_retry = 0.0
        if self.mirror is not None:
            self._sync_mirror()

    def _sync_mirror(self) -> None:
        if self.mirror is None:
            return
        try:
            self.mirror.sync(self.local.records())
        except Exception as exc:  # noqa: BLE001 - local ledger remains authoritative
            self._mirror_dirty = True
            self._mirror_error = str(exc)
            self._next_mirror_retry = time.monotonic() + _MIRROR_RETRY_SECONDS
            sys.stderr.write(f"HIVE audit PostgreSQL mirror unavailable: {exc}\n")
        else:
            self._mirror_dirty = False
            self._mirror_error = ""
            self._next_mirror_retry = 0.0

    def append(
        self,
        event_type: str,
        action: str,
        *,
        component: str,
        payload: dict[str, Any] | None = None,
        peer_id: int | None = None,
        session_id: str | None = None,
        level: str = "info",
        ts: float | None = None,
    ) -> dict[str, Any]:
        record = self.local.append_record(
            event_type,
            action,
            component=component,
            payload=payload,
            peer_id=peer_id,
            session_id=session_id,
            level=level,
            ts=ts,
        )
        if self.mirror is not None:
            if self._mirror_dirty and time.monotonic() >= self._next_mirror_retry:
                self._sync_mirror()
            elif not self._mirror_dirty:
                try:
                    self.mirror.write(record)
                except Exception as exc:  # noqa: BLE001
                    self._mirror_dirty = True
                    self._mirror_error = str(exc)
                    self._next_mirror_retry = time.monotonic() + _MIRROR_RETRY_SECONDS
                    sys.stderr.write(f"HIVE audit PostgreSQL mirror unavailable: {exc}\n")
        return record

    def list(
        self,
        *,
        after: int = 0,
        limit: int = 200,
        peer_id: int | None = None,
        event_type: str = "",
        event_types: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(limit, 1000))
        mirror_list = getattr(self.mirror, "list", None)
        if self.mirror is not None and not self._mirror_dirty and callable(mirror_list):
            try:
                return cast(
                    list[dict[str, Any]],
                    mirror_list(
                        source_id=self.local.source_id,
                        after=after,
                        limit=bounded_limit,
                        peer_id=peer_id,
                        event_type=event_type,
                        event_types=event_types,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - authoritative JSONL fallback
                self._mirror_dirty = True
                self._mirror_error = str(exc)
                self._next_mirror_retry = time.monotonic() + _MIRROR_RETRY_SECONDS
                sys.stderr.write(f"HIVE audit PostgreSQL query unavailable: {exc}\n")
        rows = [
            row
            for row in self.local.records()
            if int(row.get("sequence", 0)) > after
            and (peer_id is None or row.get("peer_id") == peer_id)
            and (not event_type or row.get("event_type") == event_type)
            and (event_types is None or row.get("event_type") in event_types)
        ]
        return rows[-bounded_limit:]

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            **self.local.status(),
            "postgres_mirror": self.mirror is not None,
            "postgres_in_sync": self.mirror is not None and not self._mirror_dirty,
            "postgres_error": self._mirror_error,
            "postgres_retry_after_s": (
                max(0.0, self._next_mirror_retry - time.monotonic())
                if self._mirror_dirty
                else 0.0
            ),
        }


_LEDGER: AuditLedger = NullAuditLedger()
_LEDGER_OVERRIDE: ContextVar[AuditLedger | None] = ContextVar(
    "hive_audit_ledger_override",
    default=None,
)
_HANDLER_INSTALLED = False
_GLOBAL_LOCK = threading.Lock()


class AuditLogHandler(logging.Handler):
    """Persist every HIVE log record without exposing configured secrets."""

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == __name__:
            return
        try:
            from hive.logging_setup import current_peer_id, current_session_id

            get_audit_ledger().append(
                "runtime_log",
                record.name,
                component=record.name,
                payload={
                    "message": _redact_log_message(record.getMessage()),
                    "module": record.module,
                    "function": record.funcName,
                    "line": record.lineno,
                    "thread": record.threadName,
                    "process": record.process,
                },
                peer_id=getattr(record, "peer_id", None) or current_peer_id(),
                session_id=current_session_id(),
                level=record.levelname.lower(),
                ts=record.created,
            )
        except Exception:
            self.handleError(record)


def configure_audit(
    path: str | Path,
    database_url: str = "",
    *,
    capture_logs: bool = True,
) -> AuditLedger:
    global _LEDGER, _HANDLER_INSTALLED
    with _GLOBAL_LOCK:
        _LEDGER = DurableAuditLedger(path, database_url)
        if capture_logs and not _HANDLER_INSTALLED:
            logging.getLogger("hive").addHandler(AuditLogHandler(level=logging.DEBUG))
            _HANDLER_INSTALLED = True
    audit_event(
        "audit_lifecycle",
        "ledger_configured",
        component="audit",
        payload={"postgres_mirror": bool(database_url), "capture_logs": capture_logs},
    )
    return _LEDGER


def get_audit_ledger() -> AuditLedger:
    return _LEDGER_OVERRIDE.get() or _LEDGER


@contextmanager
def audit_scope(ledger: AuditLedger) -> Iterator[AuditLedger]:
    """Route audit records in the current execution context to ``ledger``.

    Demo and evaluation workers use this to retain a complete audit trail
    without mixing synthetic conversation content into the operational ledger.
    """
    token = _LEDGER_OVERRIDE.set(ledger)
    try:
        yield ledger
    finally:
        _LEDGER_OVERRIDE.reset(token)


def audit_event(
    event_type: str,
    action: str,
    *,
    component: str,
    payload: dict[str, Any] | None = None,
    peer_id: int | None = None,
    session_id: str | None = None,
    level: str = "info",
    ts: float | None = None,
) -> dict[str, Any]:
    if peer_id is None or session_id is None:
        from hive.logging_setup import current_peer_id, current_session_id

        if peer_id is None:
            peer_id = current_peer_id()
        if session_id is None:
            session_id = current_session_id()
    return get_audit_ledger().append(
        event_type,
        action,
        component=component,
        payload=payload,
        peer_id=peer_id,
        session_id=session_id,
        level=level,
        ts=ts,
    )
