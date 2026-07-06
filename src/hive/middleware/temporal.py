"""Temporal noise: the tarpit delay (fyp.txt L1, "Tarpit Logic").

Computes a believable human delay before sending a reply, so response timing
does not fingerprint as a bot. The delay models reading the incoming message +
"typing" the reply + random jitter, scaled per persona (e.g. elderly = slower).

This delay also usefully masks the extra latency of the strong LLM tier
(reference-mapping.md / router synergy): escalation costs nothing in
believability because we were pausing anyway.

`compute_delay` is pure (no sleeping) so it is fully testable; `wait` performs
the actual async sleep in production.
"""

from __future__ import annotations

import asyncio
import random

from hive.logging_setup import get_logger

log = get_logger(__name__)

# Rough human texting model (seconds).
_READ_PER_CHAR = 0.02      # time to read the incoming message
_TYPE_PER_CHAR = 0.08      # time to "type" the outgoing reply
_BASE = 1.5                # pick-up-the-phone latency
_JITTER_FRAC = 0.30        # +/- randomisation
_MIN_DELAY = 2.0
_MAX_DELAY = 45.0

_PERSONA_SPEED: dict[str, float] = {
    "confused_elderly": 1.6,        # slower
    "naive_young_adult": 0.8,       # fast texter
    "overseas_worker": 1.1,
    "small_business_owner": 1.0,
}


def compute_delay(
    reply: str,
    persona: str,
    *,
    incoming_len: int = 0,
    seed: int | None = None,
) -> float:
    """Return seconds to wait before sending `reply`. Pure / no sleep."""
    rng = random.Random(seed)
    speed = _PERSONA_SPEED.get(persona, 1.0)
    raw = _BASE + incoming_len * _READ_PER_CHAR + len(reply) * _TYPE_PER_CHAR
    jitter = 1.0 + rng.uniform(-_JITTER_FRAC, _JITTER_FRAC)
    delay = max(_MIN_DELAY, min(_MAX_DELAY, raw * speed * jitter))
    log.info(
        "L1 temporal: persona=%s reply_len=%d incoming_len=%d delay=%.1fs",
        persona,
        len(reply),
        incoming_len,
        delay,
    )
    return delay


async def wait(delay: float) -> None:
    """Async sleep for `delay` seconds (production send path)."""
    await asyncio.sleep(delay)
