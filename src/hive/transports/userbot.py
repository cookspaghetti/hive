"""Telethon MTProto userbot — the data plane (fyp.txt Platform).

Logged in as the user's OWN account via a StringSession (decrypted from the
encrypted session store). Watches chats under active takeover, routes each
inbound message into the HiveEngine, waits the tarpit delay, then replies AS
THE USER. Sends a summary + evidence bundle to the control plane on hand-back.

Requires a live Telegram connection, so this is not unit-tested; the engine it
drives is (tests/test_integration.py).
"""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from hive.active_takeovers import (
    ACTIVE,
    PAUSED_AFTER_LIMIT,
    PAUSED_AFTER_RESTART,
    ActiveTakeoverStore,
)
from hive.audit import audit_event
from hive.logging_setup import get_logger
from hive.middleware.temporal import compute_phone_check_delay, wait
from hive.runtime import HiveEngine
from hive.state import Message, Phase, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)
_SAFE_MEDIA_NAME = re.compile(r"[^A-Za-z0-9._() -]+")


@dataclass(slots=True)
class ObservedChat:
    peer_id: int
    name: str
    username: str
    last_message: str
    last_message_id: int
    last_message_at: float
    message_count: int = 1
    request_pending: bool = True
    request_created_at: float | None = None
    identity_observed_at: float | None = None
    pending_messages: list[Message] = field(default_factory=list)


class UserbotTransport:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_str: str,
        engine: HiveEngine,
        on_handback: Callable[[int, SessionState], Awaitable[None]] | None = None,
        on_limit_reached: (
            Callable[[int, SessionState, str], Awaitable[None]] | None
        ) = None,
        on_takeover_request: Callable[[dict[str, object]], Awaitable[bool]] | None = None,
        *,
        inbox_debounce_s: float = 3.5,
        inbox_max_wait_s: float = 12.0,
        media_root: str | Path = "./evidence/media",
        media_max_bytes: int = 25 * 1024 * 1024,
        random_seed: int | None = None,
        checkpoint_store: ActiveTakeoverStore | None = None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self._session_str = session_str
        self.engine = engine
        # Optional async callback(peer_id, session) fired when a benign
        # conversation is handed back, so the control plane can notify the user.
        self.on_handback = on_handback
        # A turn/time budget pauses automatic replies but deliberately retains
        # the session so the operator can review and seal it.
        self.on_limit_reached = on_limit_reached
        # Optional async callback(chat) used by the operator control plane.
        self.on_takeover_request = on_takeover_request
        self.inbox_debounce_s = max(0.0, inbox_debounce_s)
        self.inbox_max_wait_s = max(self.inbox_debounce_s, inbox_max_wait_s)
        self.media_root = Path(media_root)
        self.media_max_bytes = max(0, media_max_bytes)
        self.checkpoint_store = checkpoint_store
        self._rng = random.Random(random_seed)
        self._client: Any = None
        # peer_id -> (SessionState, HashChain)
        self._sessions: dict[int, tuple[SessionState, HashChain]] = {}
        self._observed_chats: dict[int, ObservedChat] = {}
        self._notified_takeover_requests: set[int] = set()
        self._notifying_takeover_requests: set[int] = set()
        self._takeover_notification_candidates: set[int] = set()
        self._inbound_buffers: dict[int, list[Message]] = {}
        self._inbound_tasks: dict[int, asyncio.Task[Any]] = {}
        self._inbound_first_at: dict[int, float] = {}
        self._inbound_last_at: dict[int, float] = {}
        self._inbound_check_at: dict[int, float] = {}
        self._inbound_events: dict[int, asyncio.Event] = {}
        self._inflight_batches: dict[int, list[Message]] = {}
        self._media_analysis_tasks: dict[int, dict[int, asyncio.Task[Any]]] = {}
        self._takeover_seed_messages: dict[int, list[Message]] = {}
        self._paused_recoveries: set[int] = set()
        self._paused_limits: set[int] = set()
        self.restore_takeovers()

    def restore_takeovers(self) -> int:
        """Load unfinished checkpoints as operator-paused sessions."""
        if self.checkpoint_store is None:
            return 0
        restored = 0
        for checkpoint in self.checkpoint_store.list():
            session = checkpoint.session
            if (
                session.phase is Phase.CLOSING
                and checkpoint.recovery_status == PAUSED_AFTER_LIMIT
            ):
                self._paused_limits.add(session.peer_id)
            elif session.phase is Phase.CLOSING:
                minimum = int(getattr(self.engine, "early_exit_min_turns", 10))
                recoverable_benign_handback = (
                    session.verdict == "likely_benign"
                    and session.exchange_count < minimum
                )
                if recoverable_benign_handback:
                    session.phase = Phase.ACTIVE
                    audit_event(
                        "takeover_recovery",
                        "premature_benign_handback_recovered",
                        component="transport.userbot",
                        payload={
                            "exchanges": session.exchange_count,
                            "minimum_exchanges": minimum,
                        },
                        peer_id=session.peer_id,
                        session_id=session.session_id,
                        level="warning",
                    )
                else:
                    self.checkpoint_store.delete(session.session_id)
                    continue
            elif session.phase is Phase.SEALED:
                self.checkpoint_store.delete(session.session_id)
                continue
            if session.peer_id in self._sessions:
                continue
            self._sessions[session.peer_id] = (session, checkpoint.chain)
            if checkpoint.pending_messages:
                self._takeover_seed_messages[session.peer_id] = list(
                    checkpoint.pending_messages
                )
            if checkpoint.recovery_status != PAUSED_AFTER_LIMIT:
                self._paused_recoveries.add(session.peer_id)
            self._checkpoint(session.peer_id)
            audit_event(
                "takeover_recovery",
                "takeover_restored_paused",
                component="transport.userbot",
                payload={
                    "pending_messages": len(checkpoint.pending_messages),
                    "checkpoint_updated_ts": checkpoint.updated_ts,
                },
                peer_id=session.peer_id,
                session_id=session.session_id,
                level="warning",
            )
            restored += 1
        return restored

    def recovery_status(self, peer_id: int) -> str:
        if peer_id in self._paused_limits:
            return PAUSED_AFTER_LIMIT
        return PAUSED_AFTER_RESTART if peer_id in self._paused_recoveries else ACTIVE

    def is_processing(self, peer_id: int) -> bool:
        """Return whether an inbound batch is queued, analysed, or being delivered."""
        task = self._inbound_tasks.get(peer_id)
        return bool(
            self._inbound_buffers.get(peer_id)
            or self._inflight_batches.get(peer_id)
            or (task is not None and not task.done())
        )

    def _checkpoint_messages(self, peer_id: int) -> list[Message]:
        messages = [
            *self._takeover_seed_messages.get(peer_id, ()),
            *self._inflight_batches.get(peer_id, ()),
            *self._inbound_buffers.get(peer_id, ()),
        ]
        seen: set[int] = set()
        unique: list[Message] = []
        for message in messages:
            if message.msg_id not in seen:
                unique.append(message)
                seen.add(message.msg_id)
        return unique

    def _checkpoint(self, peer_id: int) -> None:
        entry = self._sessions.get(peer_id)
        if entry is None:
            return
        session, chain = entry
        if self.checkpoint_store is not None:
            self.checkpoint_store.save(
                session,
                chain,
                pending_messages=self._checkpoint_messages(peer_id),
                recovery_status=self.recovery_status(peer_id),
            )
        self._signal_change("takeover_updated", peer_id)

    @staticmethod
    def _signal_change(topic: str, peer_id: int) -> None:
        """Wake connected operator surfaces after Telegram/runtime mutations."""
        try:
            from hive.webpanel.observability import get_observation_hub

            get_observation_hub().change(topic, peer_id=peer_id)
        except Exception:
            log.exception("userbot: failed to publish state change peer=%s", peer_id)

    def checkpoint_takeover(self, peer_id: int) -> None:
        """Persist a state mutation made by another control surface."""
        self._checkpoint(peer_id)

    def update_persona(self, peer_id: int, persona: str) -> None:
        entry = self._sessions.get(peer_id)
        if entry is None:
            raise LookupError(f"no active takeover on {peer_id}")
        entry[0].persona = persona
        self._checkpoint(peer_id)

    async def resume_recovery(self, peer_id: int) -> int:
        """Explicitly resume a restored session and process its queued inbound messages."""
        if peer_id not in self._sessions:
            raise LookupError(f"no active takeover on {peer_id}")
        if peer_id not in self._paused_recoveries:
            raise ValueError(f"takeover {peer_id} is not recovery-paused")
        session = self._sessions[peer_id][0]
        self._paused_recoveries.remove(peer_id)
        self._checkpoint(peer_id)
        audit_event(
            "takeover_recovery",
            "takeover_recovery_resumed",
            component="transport.userbot",
            payload={"pending_messages": len(self._takeover_seed_messages.get(peer_id, ()))},
            peer_id=peer_id,
            session_id=session.session_id,
        )
        try:
            return await self.process_pending_takeover(peer_id)
        except Exception:
            self._paused_recoveries.add(peer_id)
            self._checkpoint(peer_id)
            raise

    def abandon_recovery(self, peer_id: int) -> tuple[SessionState, HashChain]:
        """Discard a recovery-paused checkpoint after an explicit operator action."""
        if peer_id not in self._paused_recoveries:
            raise ValueError(f"takeover {peer_id} is not recovery-paused")
        session = self._sessions[peer_id][0]
        message_count = len(session.messages)
        pending_count = len(self._takeover_seed_messages.get(peer_id, ()))
        entry = self.end_takeover(peer_id)
        if entry is None:  # guarded above; retained for a stable return type
            raise LookupError(f"no active takeover on {peer_id}")
        audit_event(
            "takeover_recovery",
            "takeover_recovery_abandoned",
            component="transport.userbot",
            payload={"messages": message_count, "pending_messages": pending_count},
            peer_id=peer_id,
            session_id=session.session_id,
            level="warning",
        )
        return entry

    async def start(self) -> None:
        """Connect as the user and register the inbound handler."""
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        self._client = TelegramClient(StringSession(self._session_str), self.api_id, self.api_hash)
        await self._client.start()  # will prompt for phone/code only if session invalid
        me = await self._client.get_me()
        log.info("userbot: connected as id=%s", getattr(me, "id", "?"))

        @self._client.on(events.NewMessage(incoming=True))  # type: ignore[untyped-decorator]
        async def _handler(event: Any) -> None:  # pragma: no cover - needs live telegram
            peer_id = event.chat_id
            text = event.raw_text or ""
            media = self._describe_media(event)
            display_text = text or self._media_placeholder(media)
            name, username, eligible = await self._event_identity(event)
            if not eligible:
                return
            received_at = time.time()
            telegram_date = getattr(event, "date", None) or getattr(
                getattr(event, "message", None), "date", None
            )
            telegram_ts = (
                telegram_date.timestamp() if telegram_date is not None else received_at
            )
            self.observe_incoming(
                peer_id,
                display_text,
                event.id,
                telegram_ts,
                name,
                username,
                captured_ts=received_at,
                media_kind=media.get("media_kind"),
                media_name=media.get("media_name"),
                media_mime=media.get("media_mime"),
                media_size=media.get("media_size"),
            )
            if peer_id not in self._sessions:
                await self._notify_takeover_request(peer_id)
                return  # only act on chats under active takeover
            media.update(await self._capture_media(event, peer_id, media))
            await self.on_message(
                peer_id,
                display_text,
                event.id,
                telegram_ts,
                captured_ts=received_at,
                media_kind=(
                    str(media["media_kind"]) if media.get("media_kind") is not None else None
                ),
                media_name=(
                    str(media["media_name"]) if media.get("media_name") is not None else None
                ),
                media_mime=(
                    str(media["media_mime"]) if media.get("media_mime") is not None else None
                ),
                media_size=(
                    cast(int, media["media_size"])
                    if isinstance(media.get("media_size"), int)
                    else None
                ),
                media_path=(
                    str(media["media_path"]) if media.get("media_path") is not None else None
                ),
                media_sha256=(
                    str(media["media_sha256"])
                    if media.get("media_sha256") is not None
                    else None
                ),
            )

        await self._seed_recent_chats()

    async def _event_identity(self, event: Any) -> tuple[str, str, bool]:  # pragma: no cover
        if not getattr(event, "is_private", False):
            return "", "", False
        try:
            sender = await event.get_sender()
        except Exception:
            return "", "", True
        eligible = not any(
            bool(getattr(sender, attribute, False))
            for attribute in ("bot", "support", "is_self")
        )
        name = " ".join(
            part
            for part in (
                getattr(sender, "first_name", ""),
                getattr(sender, "last_name", ""),
            )
            if part
        ) or str(getattr(sender, "title", "") or "")
        return name, str(getattr(sender, "username", "") or ""), eligible

    @staticmethod
    def _describe_media(event: Any) -> dict[str, object | None]:
        """Return stable, serialisable metadata for a Telethon media message."""
        message = getattr(event, "message", event)
        file = getattr(message, "file", None)
        if file is None and not getattr(message, "photo", None):
            return {}
        mime = str(getattr(file, "mime_type", "") or "")
        extension = str(getattr(file, "ext", "") or "")
        if getattr(message, "photo", None) or mime.startswith("image/"):
            kind = "image"
            extension = extension or ".jpg"
        elif getattr(message, "video_note", False):
            kind = "video note"
        elif getattr(message, "video", False) or mime.startswith("video/"):
            kind = "video"
        elif getattr(message, "voice", False):
            kind = "voice"
        elif getattr(message, "audio", False) or mime.startswith("audio/"):
            kind = "audio"
        elif getattr(message, "sticker", None):
            kind = "sticker"
        else:
            kind = "file"
        supplied_name = str(getattr(file, "name", "") or "")
        name = UserbotTransport._safe_media_name(
            supplied_name or f"telegram_{int(getattr(event, 'id', 0) or 0)}{extension}"
        )
        raw_size = getattr(file, "size", None)
        size = int(raw_size) if isinstance(raw_size, (int, float)) and raw_size >= 0 else None
        return {
            "media_kind": kind,
            "media_name": name,
            "media_mime": mime or None,
            "media_size": size,
        }

    @staticmethod
    def _safe_media_name(name: str) -> str:
        leaf = Path(str(name).replace("\\", "/")).name.strip().strip(".")
        cleaned = _SAFE_MEDIA_NAME.sub("_", leaf)[:140].strip()
        return cleaned or "telegram_file"

    @staticmethod
    def _media_placeholder(media: dict[str, object | None]) -> str:
        if not media:
            return ""
        kind = str(media.get("media_kind") or "file").capitalize()
        name = str(media.get("media_name") or "Telegram attachment")
        return f"[{kind} received: {name}]"

    async def _capture_media(
        self,
        event: Any,
        peer_id: int,
        media: dict[str, object | None],
    ) -> dict[str, object | None]:
        """Persist a bounded copy for the authenticated transcript viewer."""
        if not media or peer_id not in self._sessions:
            return {}
        session = self._sessions[peer_id][0]
        size = media.get("media_size")
        if isinstance(size, int) and size > self.media_max_bytes:
            audit_event(
                "media_capture",
                "telegram_media_skipped",
                component="transport.userbot",
                payload={
                    "msg_id": int(getattr(event, "id", 0) or 0),
                    "name": media.get("media_name"),
                    "size": size,
                    "limit": self.media_max_bytes,
                    "reason": "size_limit",
                },
                peer_id=peer_id,
                session_id=session.session_id,
                level="warning",
            )
            return {}
        filename = self._safe_media_name(str(media.get("media_name") or "telegram_file"))
        directory = self.media_root / session.session_id
        target = directory / f"{int(getattr(event, 'id', 0) or 0)}_{filename}"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            downloaded = await event.download_media(file=str(target))
            path = Path(downloaded) if downloaded else target
            if not path.is_file():
                raise OSError("Telegram did not return a downloaded media file")
            actual_size = path.stat().st_size
            if actual_size > self.media_max_bytes:
                path.unlink(missing_ok=True)
                audit_event(
                    "media_capture",
                    "telegram_media_skipped",
                    component="transport.userbot",
                    payload={
                        "msg_id": int(getattr(event, "id", 0) or 0),
                        "name": filename,
                        "size": actual_size,
                        "limit": self.media_max_bytes,
                        "reason": "downloaded_size_limit",
                    },
                    peer_id=peer_id,
                    session_id=session.session_id,
                    level="warning",
                )
                return {}
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except Exception as exc:
            target.unlink(missing_ok=True)
            log.exception("userbot: failed to capture Telegram media peer=%s", peer_id)
            audit_event(
                "media_capture",
                "telegram_media_capture_failed",
                component="transport.userbot",
                payload={
                    "msg_id": int(getattr(event, "id", 0) or 0),
                    "name": filename,
                    "error": str(exc),
                },
                peer_id=peer_id,
                session_id=session.session_id,
                level="error",
            )
            return {}
        audit_event(
            "media_capture",
            "telegram_media_captured",
            component="transport.userbot",
            payload={
                "msg_id": int(getattr(event, "id", 0) or 0),
                "name": filename,
                "mime": media.get("media_mime"),
                "size": path.stat().st_size,
                "sha256": digest,
            },
            peer_id=peer_id,
            session_id=session.session_id,
        )
        return {
            "media_path": str(path),
            "media_size": path.stat().st_size,
            "media_sha256": digest,
        }

    async def _seed_recent_chats(
        self,
        limit: int = 50,
        max_age_s: float = 7 * 24 * 60 * 60,
    ) -> None:  # pragma: no cover
        """Populate the panel inbox from recent dialogs whose latest message is inbound."""
        discovered = 0
        try:
            async for dialog in self._client.iter_dialogs(limit=limit):
                message = getattr(dialog, "message", None)
                if message is None or getattr(message, "out", False):
                    continue
                peer_id = getattr(dialog, "id", None)
                if peer_id is None:
                    continue
                entity = getattr(dialog, "entity", None)
                if not getattr(dialog, "is_user", False) or any(
                    bool(getattr(entity, attribute, False))
                    for attribute in ("bot", "support", "is_self")
                ):
                    continue
                date = getattr(message, "date", None)
                timestamp = date.timestamp() if date is not None else time.time()
                if timestamp < time.time() - max_age_s:
                    continue
                self.observe_incoming(
                    int(peer_id),
                    str(
                        getattr(message, "raw_text", "")
                        or getattr(message, "message", "")
                        or ""
                    ),
                    int(getattr(message, "id", 0) or 0),
                    timestamp,
                    str(getattr(dialog, "name", "") or ""),
                    str(getattr(entity, "username", "") or ""),
                    captured_ts=time.time(),
                    increment=False,
                    notify_request=False,
                )
                discovered += 1
        except Exception:
            log.exception("userbot: failed to discover recent inbound chats")
            return
        log.info("[startup][userbot] DISCOVERED recent_inbound_chats=%d", discovered)

    def observe_incoming(
        self,
        peer_id: int,
        text: str,
        msg_id: int,
        ts: float,
        name: str = "",
        username: str = "",
        *,
        captured_ts: float | None = None,
        media_kind: object | None = None,
        media_name: object | None = None,
        media_mime: object | None = None,
        media_size: object | None = None,
        increment: bool = True,
        notify_request: bool = True,
    ) -> None:
        current = self._observed_chats.get(peer_id)
        request_pending = peer_id not in self._sessions
        new_request = request_pending and (current is None or not current.request_pending)
        request_created_at = (
            ts
            if new_request
            else current.request_created_at
            if current and current.request_pending
            else None
        )
        captured = time.time() if captured_ts is None else captured_ts
        pending_messages = list(current.pending_messages) if current else []
        if request_pending and not any(message.msg_id == msg_id for message in pending_messages):
            pending_messages.append(
                Message(
                    role="stranger",
                    text=text,
                    ts=ts,
                    msg_id=msg_id,
                    media_kind=str(media_kind) if media_kind else None,
                    media_name=str(media_name) if media_name else None,
                    media_mime=str(media_mime) if media_mime else None,
                    media_size=int(media_size) if isinstance(media_size, (int, float)) else None,
                    captured_ts=captured,
                    platform="telegram",
                    pre_takeover=True,
                )
            )
        self._observed_chats[peer_id] = ObservedChat(
            peer_id=peer_id,
            name=name or (current.name if current else ""),
            username=username or (current.username if current else ""),
            last_message=text.strip()[:240],
            last_message_id=msg_id,
            last_message_at=ts,
            message_count=(current.message_count + 1 if current and increment else 1),
            request_pending=request_pending,
            request_created_at=request_created_at,
            identity_observed_at=captured,
            pending_messages=pending_messages,
        )
        audit_event(
            "message_observed",
            "telegram_inbound_observed",
            component="transport.userbot",
            payload={
                "msg_id": msg_id,
                "text": text,
                "ts": ts,
                "captured_ts": captured,
                "name": name,
                "username": username,
                "under_takeover": peer_id in self._sessions,
            },
            peer_id=peer_id,
            ts=ts,
        )
        self._signal_change("incoming_chat_updated", peer_id)
        if new_request:
            self._notified_takeover_requests.discard(peer_id)
            audit_event(
                "takeover_request",
                "takeover_request_created",
                component="transport.userbot",
                payload={
                    "msg_id": msg_id,
                    "text": text,
                    "name": name or (current.name if current else ""),
                    "username": username or (current.username if current else ""),
                },
                peer_id=peer_id,
                ts=ts,
            )
        if request_pending and notify_request:
            # Every new message refreshes the existing Telegram approval card.
            self._takeover_notification_candidates.add(peer_id)

    def _chat_payload(self, chat: ObservedChat) -> dict[str, object]:
        return {
            "peer_id": chat.peer_id,
            "name": chat.name,
            "display_name": chat.name,
            "username": chat.username,
            "last_message": chat.last_message,
            "latest_text": chat.last_message,
            "last_message_id": chat.last_message_id,
            "last_message_at": chat.last_message_at,
            "latest_ts": chat.last_message_at,
            "message_count": chat.message_count,
            "active": chat.peer_id in self._sessions,
            "request_pending": chat.request_pending,
            "request_created_at": chat.request_created_at,
        }

    async def _notify_takeover_request(self, peer_id: int) -> bool:
        chat = self._observed_chats.get(peer_id)
        callback = self.on_takeover_request
        if (
            chat is None
            or not chat.request_pending
            or callback is None
            or peer_id not in self._takeover_notification_candidates
            or peer_id in self._notifying_takeover_requests
        ):
            return False
        self._notifying_takeover_requests.add(peer_id)
        try:
            delivered = await callback(self._chat_payload(chat))
            if delivered is False:
                return False
        except Exception as exc:
            audit_event(
                "takeover_request",
                "takeover_request_notification_failed",
                component="transport.userbot",
                payload={"error": str(exc)},
                peer_id=peer_id,
                level="error",
            )
            log.exception("userbot: takeover request notification failed peer=%s", peer_id)
            return False
        finally:
            self._notifying_takeover_requests.discard(peer_id)
        self._notified_takeover_requests.add(peer_id)
        self._takeover_notification_candidates.discard(peer_id)
        audit_event(
            "takeover_request",
            "takeover_request_notification_sent",
            component="transport.userbot",
            payload={"channels": ["control_panel", "telegram_control_bot"]},
            peer_id=peer_id,
        )
        self._signal_change("takeover_request_notification_updated", peer_id)
        return True

    async def notify_pending_takeover_requests(self) -> None:
        """Deliver seeded/unnotified requests after the control bot is ready."""
        candidates = sorted(
            self._takeover_notification_candidates,
            key=lambda peer_id: self._observed_chats[peer_id].last_message_at,
        )
        for peer_id in candidates:
            await self._notify_takeover_request(peer_id)

    def list_observed_chats(self) -> list[dict[str, object]]:
        chats = list(self._observed_chats.values())
        chats.sort(key=lambda chat: chat.last_message_at, reverse=True)
        return [self._chat_payload(chat) for chat in chats]

    def has_pending_takeover_request(self, peer_id: int) -> bool:
        chat = self._observed_chats.get(peer_id)
        return bool(chat and chat.request_pending and peer_id not in self._sessions)

    def dismiss_takeover_request(self, peer_id: int) -> bool:
        chat = self._observed_chats.get(peer_id)
        if chat is None or not chat.request_pending or peer_id in self._sessions:
            return False
        chat.request_pending = False
        chat.request_created_at = None
        chat.pending_messages.clear()
        self._notified_takeover_requests.discard(peer_id)
        self._notifying_takeover_requests.discard(peer_id)
        self._takeover_notification_candidates.discard(peer_id)
        audit_event(
            "takeover_request",
            "takeover_request_dismissed",
            component="transport.userbot",
            payload={},
            peer_id=peer_id,
        )
        self._signal_change("takeover_request_dismissed", peer_id)
        return True

    def begin_takeover(self, peer_id: int, persona: str) -> None:
        self._clear_inbound(peer_id)
        self._paused_limits.discard(peer_id)
        observed = self._observed_chats.get(peer_id)
        pending_messages: list[Message] = []
        if observed is not None:
            pending_messages = sorted(
                observed.pending_messages,
                key=lambda message: (message.ts, message.msg_id),
            )
            observed.pending_messages.clear()
            observed.request_pending = False
            observed.request_created_at = None
        self._notified_takeover_requests.discard(peer_id)
        self._takeover_notification_candidates.discard(peer_id)
        self._sessions[peer_id] = self.engine.new_session(peer_id, persona)
        session = self._sessions[peer_id][0]
        if observed is not None:
            session.peer_display_name = observed.name
            session.peer_username = observed.username
            session.identity_observed_ts = observed.identity_observed_at
        if pending_messages:
            self._takeover_seed_messages[peer_id] = pending_messages
        try:
            self._checkpoint(peer_id)
        except Exception:
            self._sessions.pop(peer_id, None)
            self._takeover_seed_messages.pop(peer_id, None)
            raise
        audit_event(
            "takeover",
            "takeover_started",
            component="transport.userbot",
            payload={
                "persona": persona,
                "peer_display_name": session.peer_display_name,
                "peer_username": session.peer_username,
                "identity_observed_ts": session.identity_observed_ts,
                "preserved_trigger_messages": len(pending_messages),
            },
            peer_id=peer_id,
            session_id=session.session_id,
        )
        log.info("userbot: takeover started peer=%s persona=%s", peer_id, persona)

    async def process_pending_takeover(self, peer_id: int) -> int:
        """Process preserved pre-takeover messages after operator approval."""
        if peer_id in self._paused_recoveries or peer_id in self._paused_limits:
            return 0
        messages = self._takeover_seed_messages.pop(peer_id, [])
        if not messages or peer_id not in self._sessions:
            return 0
        try:
            await self._process_inbound_batch(peer_id, messages)
        except Exception:
            session = self._sessions.get(peer_id, (None, None))[0]
            processed_ids = {
                message.msg_id for message in getattr(session, "messages", ())
            }
            unprocessed = [
                message for message in messages if message.msg_id not in processed_ids
            ]
            if unprocessed:
                self._takeover_seed_messages[peer_id] = unprocessed
            self._checkpoint(peer_id)
            raise
        return len(messages)

    async def on_message(
        self,
        peer_id: int,
        text: str,
        msg_id: int,
        ts: float,
        *,
        media_kind: str | None = None,
        media_name: str | None = None,
        media_mime: str | None = None,
        media_size: int | None = None,
        media_path: str | None = None,
        media_sha256: str | None = None,
        captured_ts: float | None = None,
    ) -> None:
        if peer_id not in self._sessions:
            return
        message = Message(
            "stranger",
            text,
            ts,
            msg_id,
            media_kind=media_kind,
            media_name=media_name,
            media_mime=media_mime,
            media_size=media_size,
            media_path=media_path,
            media_sha256=media_sha256,
            captured_ts=captured_ts,
            platform="telegram",
        )
        if peer_id in self._paused_recoveries:
            queued = self._takeover_seed_messages.setdefault(peer_id, [])
            if not any(item.msg_id == message.msg_id for item in queued):
                queued.append(message)
            self._checkpoint(peer_id)
            session = self._sessions[peer_id][0]
            audit_event(
                "takeover_recovery",
                (
                    "inbound_queued_while_limit_paused"
                    if peer_id in self._paused_limits
                    else "inbound_queued_while_recovery_paused"
                ),
                component="transport.userbot",
                payload={"msg_id": msg_id, "queued_messages": len(queued)},
                peer_id=peer_id,
                session_id=session.session_id,
                ts=ts,
                level="warning",
            )
            return
        self._schedule_media_analysis(peer_id, message)
        loop = asyncio.get_running_loop()
        now = loop.time()
        self._inbound_buffers.setdefault(peer_id, []).append(message)
        self._checkpoint(peer_id)
        if peer_id not in self._inbound_first_at:
            self._inbound_first_at[peer_id] = now
            session = self._sessions[peer_id][0]
            check_delay = compute_phone_check_delay(
                session.persona,
                minimum=self.inbox_debounce_s,
                maximum=self.inbox_max_wait_s,
                pace=session.reply_pace,
                seed=self._rng.randrange(2**32),
            )
            self._inbound_check_at[peer_id] = now + check_delay
            audit_event(
                "phone_check",
                "phone_check_scheduled",
                component="transport.userbot",
                payload={
                    "delay_s": check_delay,
                    "minimum_s": self.inbox_debounce_s,
                    "maximum_s": self.inbox_max_wait_s,
                    "pace": session.reply_pace,
                },
                peer_id=peer_id,
                session_id=session.session_id,
            )
        self._inbound_last_at[peer_id] = now
        session = self._sessions[peer_id][0]
        audit_event(
            "message_buffer",
            "inbound_buffered",
            component="transport.userbot",
            payload={
                "msg_id": msg_id,
                "text": text,
                "ts": ts,
                "buffer_size": len(self._inbound_buffers[peer_id]),
                "media_kind": media_kind,
                "media_name": media_name,
                "media_mime": media_mime,
                "media_size": media_size,
                "media_sha256": media_sha256,
            },
            peer_id=peer_id,
            session_id=session.session_id,
            ts=ts,
        )
        self._inbound_events.setdefault(peer_id, asyncio.Event()).set()
        task = self._inbound_tasks.get(peer_id)
        if task is None or task.done():
            task = asyncio.create_task(self._drain_inbound(peer_id))
            self._inbound_tasks[peer_id] = task
        await asyncio.shield(task)

    async def _wait_for_inbox(self, peer_id: int) -> None:
        while peer_id in self._inbound_buffers:
            now = asyncio.get_running_loop().time()
            quiet_remaining = self.inbox_debounce_s - (
                now - self._inbound_last_at[peer_id]
            )
            max_remaining = self.inbox_max_wait_s - (
                now - self._inbound_first_at[peer_id]
            )
            check_remaining = self._inbound_check_at.get(peer_id, now) - now
            if (quiet_remaining <= 0 and check_remaining <= 0) or max_remaining <= 0:
                return
            waits = [
                value
                for value in (quiet_remaining, check_remaining, max_remaining)
                if value > 0
            ]
            await asyncio.sleep(min(waits))

    async def _drain_inbound(self, peer_id: int) -> None:
        try:
            while peer_id in self._sessions and self._inbound_buffers.get(peer_id):
                await self._wait_for_inbox(peer_id)
                batch = self._inbound_buffers.pop(peer_id, [])
                self._inbound_first_at.pop(peer_id, None)
                self._inbound_last_at.pop(peer_id, None)
                self._inbound_check_at.pop(peer_id, None)
                self._inbound_events.setdefault(peer_id, asyncio.Event()).clear()
                if batch:
                    session = self._sessions[peer_id][0]
                    audit_event(
                        "phone_check",
                        "inbound_batch_collected",
                        component="transport.userbot",
                        payload={
                            "message_count": len(batch),
                            "message_ids": [message.msg_id for message in batch],
                        },
                        peer_id=peer_id,
                        session_id=session.session_id,
                    )
                    await self._process_inbound_batch(peer_id, batch)
        finally:
            current = asyncio.current_task()
            if self._inbound_tasks.get(peer_id) is current:
                self._inbound_tasks.pop(peer_id, None)

    def _schedule_media_analysis(self, peer_id: int, message: Message) -> None:
        analyzer = getattr(self.engine, "analyze_media", None)
        if not callable(analyzer) or message.media_kind != "image" or not message.media_path:
            return
        task = asyncio.create_task(asyncio.to_thread(analyzer, message))
        self._media_analysis_tasks.setdefault(peer_id, {})[message.msg_id] = task

    async def _resolve_media_analysis(
        self,
        peer_id: int,
        batch: list[Message],
        session: SessionState,
    ) -> None:
        tasks = self._media_analysis_tasks.get(peer_id, {})
        for message in batch:
            task = tasks.pop(message.msg_id, None)
            if task is None:
                continue
            try:
                result = await task
            except Exception as exc:  # noqa: BLE001 - media failure must not block a reply
                log.exception(
                    "userbot: media analysis failed peer=%s msg=%s",
                    peer_id,
                    message.msg_id,
                )
                audit_event(
                    "media_intelligence",
                    "media_analysis_failed",
                    component="transport.userbot",
                    payload={"msg_id": message.msg_id, "error": str(exc)},
                    peer_id=peer_id,
                    session_id=session.session_id,
                    level="error",
                )
                continue
            if result:
                message.media_analysis = dict(result.get("analysis") or {})
                message.media_hvis = list(result.get("hvis") or [])
                audit_event(
                    "media_intelligence",
                    "media_analysis_completed",
                    component="transport.userbot",
                    payload={
                        **message.media_analysis,
                        "indicators": [
                            {
                                "kind": item.kind,
                                "value": item.value,
                                "confidence": item.confidence,
                                "extractor": item.extractor,
                            }
                            for item in message.media_hvis
                        ],
                    },
                    peer_id=peer_id,
                    session_id=session.session_id,
                )
        if not tasks:
            self._media_analysis_tasks.pop(peer_id, None)

    async def _process_inbound_batch(self, peer_id: int, batch: list[Message]) -> None:
        entry = self._sessions.get(peer_id)
        if entry is None:
            return
        session, chain = entry
        if session.phase in {Phase.CLOSING, Phase.SEALED}:
            return
        self._inflight_batches[peer_id] = list(batch)
        self._checkpoint(peer_id)
        await self._resolve_media_analysis(peer_id, batch, session)
        loop = asyncio.get_running_loop()
        processing_started = loop.time()
        process_messages = getattr(self.engine, "process_messages", None)
        delayed_recording = callable(getattr(self.engine, "record_outbound", None))
        try:
            if process_messages is not None:
                if delayed_recording:
                    out = await asyncio.to_thread(
                        process_messages,
                        session,
                        chain,
                        batch,
                        record_outbound=False,
                    )
                else:
                    out = await asyncio.to_thread(process_messages, session, chain, batch)
            else:
                latest = batch[-1]
                combined = Message(
                    "stranger",
                    "\n".join(message.text for message in batch if message.text),
                    latest.ts,
                    latest.msg_id,
                )
                out = await asyncio.to_thread(
                    self.engine.process_turn,
                    session,
                    chain,
                    combined,
                )
        except Exception:
            processed_ids = {message.msg_id for message in session.messages}
            unprocessed = [message for message in batch if message.msg_id not in processed_ids]
            if unprocessed:
                queued = self._takeover_seed_messages.setdefault(peer_id, [])
                queued.extend(
                    message
                    for message in unprocessed
                    if not any(existing.msg_id == message.msg_id for existing in queued)
                )
            self._inflight_batches.pop(peer_id, None)
            self._checkpoint(peer_id)
            raise
        processing_elapsed = loop.time() - processing_started
        # Persist the processed inbound state before attempting any Telegram send.
        # A crash after this point can lose a drafted reply, but cannot replay it.
        self._inflight_batches.pop(peer_id, None)
        self._checkpoint(peer_id)
        log.info("userbot: processing peer=%s inbound_batch=%d", peer_id, len(batch))
        if out.terminated:
            # A safety budget is not a benign hand-back. Keep the checkpoint and
            # stop automatic replies until the operator reviews/seals the case.
            self._paused_limits.add(peer_id)
            self._checkpoint(peer_id)
            reason = out.reason or "safety_limit"
            log.warning("userbot: automation paused peer=%s reason=%s", peer_id, reason)
            if self.on_limit_reached is not None:
                try:
                    await self.on_limit_reached(peer_id, session, reason)
                except Exception:
                    log.exception("userbot: limit notification failed peer=%s", peer_id)
                    audit_event(
                        "control_message",
                        "limit_notification_failed",
                        component="transport.userbot",
                        payload={"reason": reason},
                        peer_id=peer_id,
                        session_id=session.session_id,
                        level="error",
                    )
            audit_event(
                "takeover",
                "takeover_automation_paused",
                component="transport.userbot",
                payload={"reason": reason, "operator_action_required": True},
                peer_id=peer_id,
                session_id=session.session_id,
                level="warning",
            )
            return
        if out.handed_back:
            # The benign early-exit safeguard returns control to the user.
            self.end_takeover(peer_id)
            log.info(
                "userbot: ending takeover peer=%s (reason=%s)", peer_id, out.reason or "benign"
            )
            if self.on_handback is not None:
                try:
                    await self.on_handback(peer_id, session)
                except Exception:
                    log.exception("userbot: hand-back notification failed peer=%s", peer_id)
                    audit_event(
                        "control_message",
                        "handback_notification_failed",
                        component="transport.userbot",
                        payload={"reason": out.reason or "benign"},
                        peer_id=peer_id,
                        session_id=session.session_id,
                        level="error",
                    )
            audit_event(
                "takeover",
                "takeover_automatically_ended",
                component="transport.userbot",
                payload={"reason": out.reason or "benign"},
                peer_id=peer_id,
                session_id=session.session_id,
            )
            return
        if session.phase in {Phase.CLOSING, Phase.SEALED} or peer_id not in self._sessions:
            return
        messages = out.messages or ((out.text,) if out.text else ())
        delays = out.message_delays_s or ((out.delay_s,) if messages else ())
        typing_durations = out.message_typing_s or tuple(0.0 for _ in messages)
        for index, message in enumerate(messages):
            if peer_id not in self._sessions:
                return
            new_inbounds = list(self._inbound_buffers.get(peer_id, ()))
            if new_inbounds:
                decision = await self._steer_pending(
                    session,
                    message,
                    new_inbounds,
                    out.pace,
                )
                if decision == "send_first":
                    await self._deliver_bubble(
                        peer_id,
                        session,
                        chain,
                        message,
                        delay=1.0,
                        typing_s=min(1.0, typing_durations[index]),
                        interruptible=False,
                        delayed_recording=delayed_recording,
                    )
                else:
                    audit_event(
                        "reply_delivery",
                        "pending_reply_discarded",
                        component="transport.userbot",
                        payload={"text": message, "new_message_count": len(new_inbounds)},
                        peer_id=peer_id,
                        session_id=session.session_id,
                    )
                return
            delay = delays[index] if index < len(delays) else 1.0
            if index == 0:
                delay = max(0.7, delay - processing_elapsed)
            interrupted = not await self._deliver_bubble(
                peer_id,
                session,
                chain,
                message,
                delay=delay,
                typing_s=typing_durations[index],
                interruptible=True,
                delayed_recording=delayed_recording,
            )
            if interrupted:
                new_inbounds = list(self._inbound_buffers.get(peer_id, ()))
                if new_inbounds and (
                    await self._steer_pending(session, message, new_inbounds, out.pace)
                    == "send_first"
                ):
                    await self._deliver_bubble(
                        peer_id,
                        session,
                        chain,
                        message,
                        delay=1.0,
                        typing_s=min(1.0, typing_durations[index]),
                        interruptible=False,
                        delayed_recording=delayed_recording,
                    )
                else:
                    audit_event(
                        "reply_delivery",
                        "pending_reply_interrupted",
                        component="transport.userbot",
                        payload={"text": message, "new_message_count": len(new_inbounds)},
                        peer_id=peer_id,
                        session_id=session.session_id,
                    )
                return

    async def _steer_pending(
        self,
        session: SessionState,
        pending_text: str,
        new_inbounds: list[Message],
        pace: str,
    ) -> str:
        steer = getattr(self.engine, "steer_pending_reply", None)
        if steer is None:
            return "send_first" if pace == "fast" else "continue"
        try:
            return await asyncio.to_thread(steer, session, pending_text, new_inbounds)
        except Exception:
            audit_event(
                "reply_steering",
                "reply_steering_failed",
                component="transport.userbot",
                payload={"pending_text": pending_text, "new_message_count": len(new_inbounds)},
                peer_id=session.peer_id,
                session_id=session.session_id,
                level="error",
            )
            log.exception("userbot: reply steering failed peer=%s", session.peer_id)
            return "send_first" if pace == "fast" else "continue"

    async def _wait_interruptible(self, peer_id: int, delay: float) -> bool:
        """Wait for a delay; return True if a new inbound message interrupts it."""
        if delay <= 0:
            return bool(self._inbound_buffers.get(peer_id))
        event = self._inbound_events.setdefault(peer_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=delay)
        except TimeoutError:
            return False
        return True

    async def _typing_wait(self, peer_id: int, delay: float, *, interruptible: bool) -> bool:
        async def pause() -> bool:
            if interruptible:
                return await self._wait_interruptible(peer_id, delay)
            await wait(delay)
            return False

        action = getattr(self._client, "action", None)
        if action is None or delay <= 0:
            return await pause()
        session = self._sessions.get(peer_id, (None, None))[0]
        audit_event(
            "typing_indicator",
            "typing_indicator_started",
            component="transport.userbot",
            payload={"duration_s": delay},
            peer_id=peer_id,
            session_id=getattr(session, "session_id", None),
        )
        async with action(peer_id, "typing"):
            interrupted = await pause()
        audit_event(
            "typing_indicator",
            "typing_indicator_ended",
            component="transport.userbot",
            payload={"duration_s": delay, "interrupted": interrupted},
            peer_id=peer_id,
            session_id=getattr(session, "session_id", None),
        )
        return interrupted

    async def _deliver_bubble(
        self,
        peer_id: int,
        session: SessionState,
        chain: HashChain,
        text: str,
        *,
        delay: float,
        typing_s: float,
        interruptible: bool,
        delayed_recording: bool,
    ) -> bool:
        typing_delay = min(max(0.0, typing_s), max(0.0, delay))
        idle_delay = max(0.0, delay - typing_delay)
        if interruptible and await self._wait_interruptible(peer_id, idle_delay):
            return False
        if await self._typing_wait(peer_id, typing_delay, interruptible=interruptible):
            return False
        if peer_id not in self._sessions:
            return False
        if interruptible and self._inbound_buffers.get(peer_id):
            return False
        audit_event(
            "reply_delivery",
            "telegram_send_attempted",
            component="transport.userbot",
            payload={"text": text, "planned_delay_s": delay, "typing_s": typing_delay},
            peer_id=peer_id,
            session_id=session.session_id,
        )
        try:
            await self.send_as_user(peer_id, text)
        except Exception as exc:
            audit_event(
                "reply_delivery",
                "telegram_send_failed",
                component="transport.userbot",
                payload={"text": text, "error": str(exc)},
                peer_id=peer_id,
                session_id=session.session_id,
                level="error",
            )
            raise
        audit_event(
            "reply_delivery",
            "telegram_send_succeeded",
            component="transport.userbot",
            payload={"text": text},
            peer_id=peer_id,
            session_id=session.session_id,
        )
        if delayed_recording:
            self.engine.record_outbound(session, chain, text)
        self._checkpoint(peer_id)
        return True

    async def send_as_user(self, peer_id: int, text: str) -> None:  # pragma: no cover
        if self._client is None:
            raise RuntimeError("userbot not started")
        await self._client.send_message(peer_id, text)

    def end_takeover(self, peer_id: int) -> tuple[SessionState, HashChain] | None:
        entry = self._sessions.get(peer_id)
        pending = self._takeover_seed_messages.pop(peer_id, [])
        if entry is not None and pending:
            session, chain = entry
            for message in pending:
                session.messages.append(message)
                chain.append(
                    {
                        "event": "msg_in",
                        "msg_id": message.msg_id,
                        "text": message.text,
                        "platform_ts": message.ts,
                        "captured_ts": message.captured_ts,
                        "platform": message.platform,
                        "pre_takeover": True,
                    },
                    ts=message.ts,
                )
            session.turn_count += len(pending)
            session.exchange_count += 1
            audit_event(
                "message",
                "pre_takeover_messages_preserved_without_reply",
                component="transport.userbot",
                payload={"message_ids": [message.msg_id for message in pending]},
                peer_id=peer_id,
                session_id=session.session_id,
                level="warning",
            )
        self._clear_inbound(peer_id)
        entry = self._sessions.get(peer_id)
        if entry is not None:
            if self.checkpoint_store is not None:
                self.checkpoint_store.delete(entry[0].session_id)
            removed = self._sessions.pop(peer_id, None)
            if removed is None:
                return None
            entry = removed
            self._paused_recoveries.discard(peer_id)
            self._paused_limits.discard(peer_id)
            audit_event(
                "takeover",
                "takeover_ended",
                component="transport.userbot",
                payload={"phase": str(entry[0].phase)},
                peer_id=peer_id,
                session_id=entry[0].session_id,
            )
            forget = getattr(self.engine, "forget", None)
            if callable(forget):
                forget(peer_id)
            self._signal_change("takeover_ended", peer_id)
        return entry

    def _clear_inbound(self, peer_id: int) -> None:
        self._inbound_buffers.pop(peer_id, None)
        self._inbound_first_at.pop(peer_id, None)
        self._inbound_last_at.pop(peer_id, None)
        self._inbound_check_at.pop(peer_id, None)
        self._inbound_events.pop(peer_id, None)
        self._inflight_batches.pop(peer_id, None)
        task = self._inbound_tasks.pop(peer_id, None)
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not None and task is not current and not task.done():
            task.cancel()
        for media_task in self._media_analysis_tasks.pop(peer_id, {}).values():
            if not media_task.done():
                media_task.cancel()

    async def run_forever(self) -> None:  # pragma: no cover
        if self._client is None:
            raise RuntimeError("userbot not started")
        await self._client.run_until_disconnected()

    async def stop(self) -> None:  # pragma: no cover - needs live telegram
        """Disconnect the data plane while preserving in-memory session state."""
        tasks = list(self._inbound_tasks.values())
        self._inbound_tasks.clear()
        self._inbound_buffers.clear()
        self._inbound_first_at.clear()
        self._inbound_last_at.clear()
        self._inbound_check_at.clear()
        self._inbound_events.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            log.info("userbot: disconnected")
