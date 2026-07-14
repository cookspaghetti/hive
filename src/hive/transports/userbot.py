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

from hive.logging_setup import get_logger
from hive.middleware.temporal import wait
from hive.runtime import HiveEngine
from hive.state import Message, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


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
            if peer_id not in self._sessions:
                return  # only act on chats under active takeover
            await self.on_message(peer_id, event.raw_text or "", event.id, time.time())

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
