"""Durable checkpoints for takeover sessions that have not been sealed yet."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from hive.state import HVI, Message, Phase, SessionState
from hive.vault.hashchain import ChainEntry, HashChain

SCHEMA_VERSION = 1
ACTIVE = "active"
PAUSED_AFTER_RESTART = "paused_after_restart"


@dataclass(slots=True)
class ActiveTakeoverCheckpoint:
    session: SessionState
    chain: HashChain
    pending_messages: list[Message]
    recovery_status: str
    updated_ts: float


class ActiveTakeoverStore(Protocol):
    def save(
        self,
        session: SessionState,
        chain: HashChain,
        *,
        pending_messages: list[Message] | None = None,
        recovery_status: str = ACTIVE,
    ) -> None: ...

    def list(self) -> list[ActiveTakeoverCheckpoint]: ...

    def delete(self, session_id: str) -> None: ...


def _hvi_from_dict(value: dict[str, Any]) -> HVI:
    return HVI(
        kind=str(value.get("kind") or "unknown"),
        value=str(value.get("value") or ""),
        source_msg_id=int(value.get("source_msg_id") or 0),
        confidence=float(value.get("confidence", 1.0)),
        extractor=str(value.get("extractor") or "unknown"),
    )


def _message_from_dict(value: dict[str, Any]) -> Message:
    return Message(
        role=value.get("role", "system"),
        text=str(value.get("text") or ""),
        ts=float(value.get("ts") or 0),
        msg_id=int(value.get("msg_id") or 0),
        media_kind=value.get("media_kind"),
        media_name=value.get("media_name"),
        media_mime=value.get("media_mime"),
        media_size=value.get("media_size"),
        media_path=value.get("media_path"),
        media_sha256=value.get("media_sha256"),
        media_analysis=value.get("media_analysis"),
        media_hvis=[_hvi_from_dict(item) for item in value.get("media_hvis", [])],
        captured_ts=value.get("captured_ts"),
        platform=str(value.get("platform") or "telegram"),
        pre_takeover=bool(value.get("pre_takeover", False)),
    )


def _session_from_dict(value: dict[str, Any]) -> SessionState:
    session = SessionState(
        peer_id=int(value["peer_id"]),
        persona=str(value["persona"]),
        session_id=str(value["session_id"]),
        phase=Phase(value.get("phase", Phase.IDLE)),
        messages=[_message_from_dict(item) for item in value.get("messages", [])],
        hvis=[_hvi_from_dict(item) for item in value.get("hvis", [])],
        indicator_reviews=list(value.get("indicator_reviews", [])),
        sandbox_results=list(value.get("sandbox_results", [])),
        verdict_score=float(value.get("verdict_score", 0.0)),
        verdict=value.get("verdict", "inconclusive"),
        turn_count=int(value.get("turn_count", 0)),
        exchange_count=int(value.get("exchange_count", 0)),
        next_agent_msg_id=int(value.get("next_agent_msg_id", -1)),
        reply_pace=value.get("reply_pace", "normal"),
        started_ts=value.get("started_ts"),
        signal_trail=list(value.get("signal_trail", [])),
        replay_of=value.get("replay_of"),
        media_analysis=list(value.get("media_analysis", [])),
        related_cases=list(value.get("related_cases", [])),
        case_probe_context=str(value.get("case_probe_context") or ""),
        case_retrieved_at_turn=int(value.get("case_retrieved_at_turn", 0)),
        case_indicator_count_at_retrieval=int(
            value.get("case_indicator_count_at_retrieval", 0)
        ),
        peer_display_name=str(value.get("peer_display_name") or ""),
        peer_username=str(value.get("peer_username") or ""),
        identity_observed_ts=value.get("identity_observed_ts"),
    )
    return session


def _payload(
    session: SessionState,
    chain: HashChain,
    pending_messages: list[Message],
    recovery_status: str,
) -> dict[str, Any]:
    if recovery_status not in {ACTIVE, PAUSED_AFTER_RESTART}:
        raise ValueError(f"unsupported recovery status: {recovery_status}")
    if not chain.verify():
        raise ValueError("cannot checkpoint an invalid evidence hash chain")
    return {
        "schema_version": SCHEMA_VERSION,
        "recovery_status": recovery_status,
        "updated_ts": time.time(),
        "session": asdict(session),
        "chain": [asdict(entry) for entry in chain.entries],
        "pending_messages": [asdict(message) for message in pending_messages],
    }


def _checkpoint_from_payload(value: dict[str, Any]) -> ActiveTakeoverCheckpoint:
    if int(value.get("schema_version", 0)) != SCHEMA_VERSION:
        raise ValueError("unsupported active takeover checkpoint schema")
    session = _session_from_dict(dict(value["session"]))
    chain = HashChain(
        entries=[ChainEntry(**dict(item)) for item in value.get("chain", [])]
    )
    if not chain.verify():
        raise ValueError("active takeover checkpoint has an invalid evidence hash chain")
    return ActiveTakeoverCheckpoint(
        session=session,
        chain=chain,
        pending_messages=[
            _message_from_dict(dict(item)) for item in value.get("pending_messages", [])
        ],
        recovery_status=str(value.get("recovery_status") or ACTIVE),
        updated_ts=float(value.get("updated_ts") or 0),
    )


class FileActiveTakeoverStore:
    """Atomic JSON checkpoints for local and test deployments."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def save(
        self,
        session: SessionState,
        chain: HashChain,
        *,
        pending_messages: list[Message] | None = None,
        recovery_status: str = ACTIVE,
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{session.session_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                _payload(session, chain, pending_messages or [], recovery_status),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)

    def list(self) -> list[ActiveTakeoverCheckpoint]:
        if not self.root.exists():
            return []
        checkpoints: list[ActiveTakeoverCheckpoint] = []
        for path in self.root.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                checkpoints.append(_checkpoint_from_payload(raw))
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return sorted(checkpoints, key=lambda item: item.updated_ts, reverse=True)

    def delete(self, session_id: str) -> None:
        (self.root / f"{session_id}.json").unlink(missing_ok=True)


class PostgresActiveTakeoverStore:
    """PostgreSQL checkpoints used by the Compose deployment."""

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
                    CREATE TABLE IF NOT EXISTS active_takeovers (
                        session_id TEXT PRIMARY KEY,
                        peer_id BIGINT NOT NULL UNIQUE,
                        updated_ts DOUBLE PRECISION NOT NULL,
                        checkpoint JSONB NOT NULL
                    )
                    """
                )
            connection.commit()
            self._initialized = True
        return connection

    def save(
        self,
        session: SessionState,
        chain: HashChain,
        *,
        pending_messages: list[Message] | None = None,
        recovery_status: str = ACTIVE,
    ) -> None:
        from psycopg.types.json import Jsonb

        checkpoint = _payload(
            session,
            chain,
            pending_messages or [],
            recovery_status,
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO active_takeovers (session_id, peer_id, updated_ts, checkpoint)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (peer_id) DO UPDATE SET
                    session_id = EXCLUDED.session_id,
                    updated_ts = EXCLUDED.updated_ts,
                    checkpoint = EXCLUDED.checkpoint
                """,
                (
                    session.session_id,
                    session.peer_id,
                    checkpoint["updated_ts"],
                    Jsonb(checkpoint),
                ),
            )

    def list(self) -> list[ActiveTakeoverCheckpoint]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT checkpoint FROM active_takeovers ORDER BY updated_ts DESC"
            )
            return [_checkpoint_from_payload(dict(row[0])) for row in cursor.fetchall()]

    def delete(self, session_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM active_takeovers WHERE session_id = %s",
                (session_id,),
            )


def build_active_takeover_store(
    root: str | Path,
    database_url: str = "",
) -> ActiveTakeoverStore:
    if database_url:
        return PostgresActiveTakeoverStore(database_url)
    return FileActiveTakeoverStore(root)
