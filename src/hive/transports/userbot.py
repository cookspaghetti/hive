"""Telethon MTProto userbot — the data plane (fyp.txt Platform).

Logged in as the user's OWN account. Watches the target stranger chat, routes
each inbound message into the HiveEngine, waits the tarpit delay, then replies
AS THE USER. Session bytes come from the encrypted security.session_store.

Runtime (Telegram) behaviour is not unit-tested here; the engine it calls is.
"""

from __future__ import annotations

import asyncio

from hive.logging_setup import get_logger
from hive.middleware.temporal import wait
from hive.runtime import HiveEngine
from hive.state import Message, SessionState
from hive.vault.hashchain import HashChain

log = get_logger(__name__)


class UserbotTransport:
    def __init__(self, api_id: int, api_hash: str, session_bytes: bytes, engine: HiveEngine) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self.engine = engine
        self._client = None  # telethon.TelegramClient, built in start()
        # Active takeovers: peer_id -> (SessionState, HashChain)
        self._sessions: dict[int, tuple[SessionState, HashChain]] = {}

    async def start(self) -> None:
        """Connect and register the inbound handler.

        TODO(build): construct telethon.TelegramClient from the in-memory
        session (StringSession), register events.NewMessage for takeover peers.
        """
        raise NotImplementedError

    def begin_takeover(self, peer_id: int, persona: str) -> None:
        self._sessions[peer_id] = self.engine.new_session(peer_id, persona)
        log.info("userbot: takeover started peer=%d persona=%s", peer_id, persona)

    async def on_message(self, peer_id: int, text: str, msg_id: int, ts: float) -> None:
        """Handle an inbound stranger message for an active takeover."""
        entry = self._sessions.get(peer_id)
        if entry is None:
            return  # not under takeover; ignore
        session, chain = entry
        out = self.engine.process_turn(session, chain, Message("stranger", text, ts, msg_id))
        if out.handed_back:
            log.info("userbot: handed conversation back to user peer=%d", peer_id)
            return
        if out.text is not None:
            await wait(out.delay_s)          # tarpit delay (masks strong-model latency)
            await self.send_as_user(peer_id, out.text)

    async def send_as_user(self, peer_id: int, text: str) -> None:
        """Send `text` as the user into the stranger chat.

        TODO(build): self._client.send_message(peer_id, text).
        """
        raise NotImplementedError

    def end_takeover(self, peer_id: int) -> tuple[SessionState, HashChain] | None:
        return self._sessions.pop(peer_id, None)
