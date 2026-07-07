"""In-session memory for Layer 2 (fyp.txt L2).

Retains details revealed incrementally across turns (a bank account in one
message, a Telegram ID in another) and surfaces them when relevant, so the
persona can refer back to earlier disclosures without re-reading the whole
transcript.

Backends are injected via the `MemoryBackend` protocol:
- `KeywordMemory`  — dependency-free default; deterministic keyword recall.
  Makes the feature work (and testable) with no external services.
- `Mem0Memory`     — production/semantic recall via mem0 + Qdrant (lazy import).
  Drop-in upgrade when a Qdrant instance is available.

The engine creates one backend per conversation (keyed by peer id).
"""

from __future__ import annotations

import re
from typing import Protocol

from hive.logging_setup import get_logger

log = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]{4,}")


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower()))


class MemoryBackend(Protocol):
    def add(self, role: str, text: str) -> None: ...
    def recall(self, query: str, k: int = 5) -> list[str]: ...


class NullMemory:
    """No-op memory. Use to disable recall entirely."""

    def add(self, role: str, text: str) -> None:  # noqa: D401
        return None

    def recall(self, query: str, k: int = 5) -> list[str]:
        return []


class KeywordMemory:
    """Deterministic, dependency-free recall over the conversation.

    Stores every message and, on recall, returns the most recent *stranger*
    messages that share a keyword with the query (excluding the query text
    itself). Good enough to demonstrate incremental-detail retention and fully
    testable offline; swap for Mem0Memory for semantic recall.
    """

    def __init__(self, peer_id: int) -> None:
        self.peer_id = peer_id
        self._entries: list[tuple[str, str]] = []  # (role, text)

    def add(self, role: str, text: str) -> None:
        if text:
            self._entries.append((role, text))

    def recall(self, query: str, k: int = 5) -> list[str]:
        q = _tokens(query)
        if not q:
            return []
        hits: list[str] = []
        # newest first, so the most recent relevant disclosures win
        for role, text in reversed(self._entries):
            if role != "stranger" or text == query:
                continue
            if _tokens(text) & q:
                hits.append(text)
            if len(hits) >= k:
                break
        if hits:
            log.info("L2 memory: recalled %d prior detail(s) for peer=%s", len(hits), self.peer_id)
        return hits


class Mem0Memory:
    """Semantic memory via mem0 backed by Qdrant (lazy import).

    Requires a running Qdrant (HIVE_QDRANT_URL) and mem0's configured embedder.
    Not exercised by the offline test suite.
    """

    def __init__(self, peer_id: int, qdrant_url: str) -> None:  # pragma: no cover
        from mem0 import Memory

        self.peer_id = peer_id
        self._user = f"peer:{peer_id}"
        self._mem = Memory.from_config(
            {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {"url": qdrant_url, "collection_name": "hive_sessions"},
                }
            }
        )

    def add(self, role: str, text: str) -> None:  # pragma: no cover
        if text:
            self._mem.add(text, user_id=self._user, metadata={"role": role})

    def recall(self, query: str, k: int = 5) -> list[str]:  # pragma: no cover
        res = self._mem.search(query, user_id=self._user, limit=k)
        items = res.get("results", res) if isinstance(res, dict) else res
        return [r.get("memory", "") for r in items if r.get("memory")]


def build_memory(peer_id: int, settings=None):
    """Factory: Mem0 if a Qdrant URL is configured, else the offline default."""
    if settings is not None and getattr(settings, "use_semantic_memory", False):
        try:  # pragma: no cover - needs live qdrant/mem0
            return Mem0Memory(peer_id, settings.qdrant_url)
        except Exception as exc:  # noqa: BLE001
            log.warning("L2 memory: mem0/qdrant unavailable (%s); using KeywordMemory", exc)
    return KeywordMemory(peer_id)
