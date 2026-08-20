"""Shared takeover lifecycle operations for panel and Telegram controls."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hive.analysis_runs import model_manifest
from hive.audit import audit_event
from hive.case_intelligence import CaseIntelligenceStore, build_case_profile
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
        case_intelligence: CaseIntelligenceStore | None = None,
    ) -> None:
        self.engine = engine
        self.userbot = userbot
        self.settings = settings
        self.history = history
        self.evidence_root = Path(evidence_root)
        self.case_intelligence = case_intelligence
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
                record = self.history.archive(
                    session,
                    evidence_path=sealed_path,
                    analysis_models=model_manifest(self.settings),
                )
                self._index_case(record)
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

    def _index_case(self, record: dict[str, Any]) -> None:
        if self.case_intelligence is None:
            return
        try:
            self.case_intelligence.index(build_case_profile(record))
        except Exception as exc:  # noqa: BLE001 - enrichment must not invalidate sealed evidence
            audit_event(
                "case_intelligence",
                "sealed_case_index_failed",
                component="takeover.coordinator",
                payload={"case_id": record.get("id"), "error": str(exc)},
                peer_id=int(record["peer_id"]),
                session_id=str(record.get("session_id") or "") or None,
                level="error",
            )
