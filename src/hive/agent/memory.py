"""In-session memory: mem0 backed by Qdrant (fyp.txt L2).

Retains details revealed incrementally (a bank account in one message, a
Telegram ID in another) and surfaces them when relevant.
"""

from __future__ import annotations


class SessionMemory:
    def __init__(self, peer_id: int, qdrant_url: str) -> None:
        self.peer_id = peer_id
        self.qdrant_url = qdrant_url
        # TODO: init mem0 client bound to a per-peer namespace.

    def add(self, role: str, text: str) -> None:
        raise NotImplementedError

    def recall(self, query: str, k: int = 5) -> list[str]:
        raise NotImplementedError
