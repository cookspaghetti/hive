"""Telethon MTProto userbot — the data plane (fyp.txt Platform).

Logged in as the user's OWN account via a StringSession (decrypted from the
encrypted session store). Watches chats under active takeover, routes each
inbound message into the HiveEngine, waits the tarpit delay, then replies AS
THE USER. Sends a summary + evidence bundle to the control plane on hand-back.

Requires a live Telegram connection, so this is not unit-tested; the engine it
drives is (tests/test_integration.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from hive.logging_setup import get_logger
from hive.middleware.temporal import wait
from hive.runtime import HiveEngine
from hive.state import Message, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


@dataclass(slots=True)
class ObservedChat:
    peer_id: int
    name: str
    username: str
    last_message: str
    last_message_id: int
    last_message_at: float
    message_count: int = 1


class UserbotTransport:
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_str: str,
        engine: HiveEngine,
        on_handback=None,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self._session_str = session_str
        self.engine = engine
        # Optional async callback(peer_id, session) fired when a benign
        # conversation is handed back, so the control plane can notify the user.
        self.on_handback = on_handback
        self._client = None
        # peer_id -> (SessionState, HashChain)
        self._sessions: dict[int, tuple[SessionState, HashChain]] = {}
        self._observed_chats: dict[int, ObservedChat] = {}

    async def start(self) -> None:
        """Connect as the user and register the inbound handler."""
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession

        self._client = TelegramClient(StringSession(self._session_str), self.api_id, self.api_hash)
        await self._client.start()  # will prompt for phone/code only if session invalid
        me = await self._client.get_me()
        log.info("userbot: connected as id=%s", getattr(me, "id", "?"))

        @self._client.on(events.NewMessage(incoming=True))
        async def _handler(event):  # pragma: no cover - needs live telegram
            peer_id = event.chat_id
            text = event.raw_text or ""
            name, username, eligible = await self._event_identity(event)
            if not eligible:
                return
            self.observe_incoming(peer_id, text, event.id, time.time(), name, username)
            if peer_id not in self._sessions:
                return  # only act on chats under active takeover
            await self.on_message(peer_id, text, event.id, time.time())

        await self._seed_recent_chats()

    async def _event_identity(self, event) -> tuple[str, str, bool]:  # pragma: no cover
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
                    increment=False,
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
        increment: bool = True,
    ) -> None:
        current = self._observed_chats.get(peer_id)
        self._observed_chats[peer_id] = ObservedChat(
            peer_id=peer_id,
            name=name or (current.name if current else ""),
            username=username or (current.username if current else ""),
            last_message=text.strip()[:240],
            last_message_id=msg_id,
            last_message_at=ts,
            message_count=(current.message_count + 1 if current and increment else 1),
        )

    def list_observed_chats(self) -> list[dict[str, object]]:
        chats = list(self._observed_chats.values())
        chats.sort(key=lambda chat: chat.last_message_at, reverse=True)
        return [
            {
                "peer_id": chat.peer_id,
                "name": chat.name,
                "username": chat.username,
                "last_message": chat.last_message,
                "last_message_id": chat.last_message_id,
                "last_message_at": chat.last_message_at,
                "message_count": chat.message_count,
                "active": chat.peer_id in self._sessions,
            }
            for chat in chats
        ]

    def begin_takeover(self, peer_id: int, persona: str) -> None:
        self._sessions[peer_id] = self.engine.new_session(peer_id, persona)
        log.info("userbot: takeover started peer=%s persona=%s", peer_id, persona)

    async def on_message(self, peer_id: int, text: str, msg_id: int, ts: float) -> None:
        entry = self._sessions.get(peer_id)
        if entry is None:
            return
        session, chain = entry
        out = self.engine.process_turn(session, chain, Message("stranger", text, ts, msg_id))
        if out.handed_back or out.terminated:
            # Stop intercepting this peer. handed_back = benign safeguard;
            # terminated = turn/duration budget exhausted (fyp.txt S8). Either
            # way, drop the session and let the operator seal via /stop.
            self.end_takeover(peer_id)
            log.info(
                "userbot: ending takeover peer=%s (reason=%s)", peer_id, out.reason or "benign"
            )
            if self.on_handback is not None:
                await self.on_handback(peer_id, session)
            return
        if out.text is not None:
            await wait(out.delay_s)  # tarpit delay (masks strong-model latency)
            await self.send_as_user(peer_id, out.text)

    async def send_as_user(self, peer_id: int, text: str) -> None:  # pragma: no cover
        if self._client is None:
            raise RuntimeError("userbot not started")
        await self._client.send_message(peer_id, text)

    def end_takeover(self, peer_id: int) -> tuple[SessionState, HashChain] | None:
        return self._sessions.pop(peer_id, None)

    async def run_forever(self) -> None:  # pragma: no cover
        if self._client is None:
            raise RuntimeError("userbot not started")
        await self._client.run_until_disconnected()

    async def stop(self) -> None:  # pragma: no cover - needs live telegram
        """Disconnect the data plane while preserving in-memory session state."""
        if self._client is not None:
            await self._client.disconnect()
            self._client = None
            log.info("userbot: disconnected")
