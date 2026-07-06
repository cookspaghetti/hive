"""Telethon MTProto userbot — the data plane (fyp.txt Platform).

Logged in as the user's OWN account. Watches real chats, routes a target
stranger's messages into the orchestrator, and replies as the user (with L1
middleware applied before send). The session file is loaded via the encrypted
security.session_store.
"""

from __future__ import annotations


class UserbotTransport:
    def __init__(self, api_id: int, api_hash: str, session_bytes: bytes) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        # TODO: build telethon.TelegramClient from in-memory session.

    async def start(self) -> None:
        """Connect and register the NewMessage handler for the target peer."""
        raise NotImplementedError

    async def send_as_user(self, peer_id: int, text: str) -> None:
        raise NotImplementedError
