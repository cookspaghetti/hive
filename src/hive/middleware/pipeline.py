"""L1 middleware entry point (fyp.txt L1).

Combines linguistic + temporal noise into a single post-processing step that
sits between the L2 reason node and the transport send. Returns the noised
text and the delay to apply; the caller (send path) performs the actual wait
so this stays synchronous and testable.
"""

from __future__ import annotations

from dataclasses import dataclass

from hive.middleware.linguistic import inject_noise
from hive.middleware.temporal import compute_delay


@dataclass
class MiddlewareResult:
    text: str      # noised reply to send
    delay_s: float  # seconds to wait before sending


def apply(
    raw_reply: str,
    persona: str,
    *,
    incoming_len: int = 0,
    seed: int | None = None,
) -> MiddlewareResult:
    """Apply human-emulation noise to a raw agent reply."""
    text = inject_noise(raw_reply, persona, seed=seed)
    delay = compute_delay(raw_reply, persona, incoming_len=incoming_len, seed=seed)
    return MiddlewareResult(text=text, delay_s=delay)
