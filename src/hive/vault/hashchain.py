"""SHA-256 hash chain (fyp.txt L5).

Each record's hash incorporates the previous hash, so tampering with any
single entry invalidates the rest of the chain. Timestamps should be anchored
to a trusted external time source (NTP), not the local clock (fyp.txt L5
admissibility caveat).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChainEntry:
    index: int
    ts: float
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str = ""


@dataclass
class HashChain:
    entries: list[ChainEntry] = field(default_factory=list)

    def _hash(self, index: int, ts: float, payload: dict[str, Any], prev_hash: str) -> str:
        blob = json.dumps(
            {"i": index, "ts": ts, "payload": payload, "prev": prev_hash},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(blob).hexdigest()

    def append(self, payload: dict[str, Any], ts: float) -> ChainEntry:
        index = len(self.entries)
        prev = self.entries[-1].entry_hash if self.entries else "0" * 64
        h = self._hash(index, ts, payload, prev)
        entry = ChainEntry(index=index, ts=ts, payload=payload, prev_hash=prev, entry_hash=h)
        self.entries.append(entry)
        return entry

    def verify(self) -> bool:
        """Recompute the chain; return False if any link is broken."""
        prev = "0" * 64
        for e in self.entries:
            if e.prev_hash != prev:
                return False
            if self._hash(e.index, e.ts, e.payload, e.prev_hash) != e.entry_hash:
                return False
            prev = e.entry_hash
        return True
