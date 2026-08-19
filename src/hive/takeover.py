"""Shared takeover lifecycle operations for panel and Telegram controls."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hive.audit import audit_event
from hive.history import HistoryStore
from hive.vault.paths import new_bundle_path


class TakeoverNotFoundError(LookupError):
    """The requested peer has no active takeover."""


class TakeoverBusyError(RuntimeError):
    """The takeover is already being sealed by another control surface."""


class TakeoverSealError(RuntimeError):
    """Report generation or archival failed; the takeover remains active."""


@dataclass(frozen=True, slots=True)
class SealedTakeover:
    peer_id: int
    session: Any
    path: Path
    history_record: dict[str, Any]
    summary: str


class TakeoverCoordinator:
    """One authoritative stop-and-seal service shared by every control plane."""

    def __init__(
        self,
        engine: Any,
        userbot: Any,
        settings: Any,
        history: HistoryStore,
        *,
        evidence_root: str | Path,
    ) -> None:
        self.engine = engine
        self.userbot = userbot
        self.settings = settings
        self.history = history
        self.evidence_root = Path(evidence_root)
        self._guard = threading.Lock()
        self._sealing: set[int] = set()

    def seal(self, peer_id: int) -> SealedTakeover:
        """Seal exactly once, retaining the live takeover on any failure."""
        with self._guard:
            if peer_id in self._sealing:
                raise TakeoverBusyError(f"takeover {peer_id} is already being sealed")
            if peer_id not in self.userbot._sessions:
                raise TakeoverNotFoundError(f"no active takeover on {peer_id}")
            self._sealing.add(peer_id)

        try:
            entry = self.userbot._sessions.get(peer_id)
            if entry is None:
                raise TakeoverNotFoundError(f"no active takeover on {peer_id}")
            session, chain = entry
            previous_phase = session.phase
            out_path = new_bundle_path(self.evidence_root, session)
            audit_event(
                "takeover",
                "stop_and_seal_requested",
                component="takeover.coordinator",
                payload={"out_path": str(out_path)},
                peer_id=peer_id,
                session_id=session.session_id,
            )
            try:
                sealed_path = Path(
                    self.engine.close_session(
                        session,
                        chain,
                        str(out_path),
                        self.settings.signing_key_path,
                        operator_name=getattr(self.settings, "operator_name", ""),
                    )
                )
                record = self.history.archive(session, evidence_path=sealed_path)
            except Exception as exc:
                session.phase = previous_phase
                audit_event(
                    "takeover",
                    "stop_and_seal_failed",
                    component="takeover.coordinator",
                    payload={"error": str(exc)},
                    peer_id=peer_id,
                    session_id=session.session_id,
                    level="error",
                )
                raise TakeoverSealError(str(exc)) from exc

            self.userbot.end_takeover(peer_id)
            result = SealedTakeover(
                peer_id=peer_id,
                session=session,
                path=sealed_path,
                history_record=record,
                summary=self.engine.summary(session),
            )
            audit_event(
                "takeover",
                "stop_and_seal_completed",
                component="takeover.coordinator",
                payload={
                    "bundle": str(sealed_path),
                    "history_id": record.get("id"),
                },
                peer_id=peer_id,
                session_id=session.session_id,
            )
            return result
        finally:
            with self._guard:
                self._sealing.discard(peer_id)
