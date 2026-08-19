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
import re

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

_PHONE_CHECK_BIAS: dict[str, float] = {
    "confused_elderly": 0.68,
    "naive_young_adult": 0.32,
    "overseas_worker": 0.72,
    "small_business_owner": 0.48,
}

_PACE_MULTIPLIER = {"fast": 0.62, "normal": 1.0, "slow": 1.45}
_SLOW_CONTEXT = re.compile(
    r"\b(?:no rush|later|after (?:my |your |the )?shift|after work|"
    r"when (?:you are|you're|ur|youre) free|can wait|busy|at work|"
    r"outside now|can(?:not|'t) (?:do|check|open|talk).{0,20}now)\b|"
    r"不急|晚点|有空|下班|在忙",
    re.IGNORECASE,
)


def calibrate_pace(pace: str, *, incoming_text: str, reply_text: str) -> str:
    """Keep model pacing coherent when an obvious slow-context cue appears."""
    if pace == "normal" and _SLOW_CONTEXT.search(f"{incoming_text}\n{reply_text}"):
        log.info("L1 temporal: calibrated model pace normal -> slow")
        return "slow"
    return pace


def compute_phone_check_delay(
    persona: str,
    *,
    minimum: float,
    maximum: float,
    pace: str = "normal",
    seed: int | None = None,
) -> float:
    """Choose when the persona next checks the chat within configured bounds.

    A triangular distribution avoids the machine-like fixed debounce while
    retaining a strict upper bound. Persona bias makes a young adult check
    sooner and a distracted worker or elderly persona check later.
    """
    low = max(0.0, minimum)
    high = max(low, maximum)
    if high == low:
        return low
    bias = _PHONE_CHECK_BIAS.get(persona, 0.5)
    if pace == "fast":
        bias *= 0.45
    elif pace == "slow":
        bias = 0.62 + bias * 0.35
    mode = low + (high - low) * bias
    return random.Random(seed).triangular(low, high, mode)


def compute_typing_duration(
    reply: str,
    persona: str,
    *,
    pace: str = "normal",
    seed: int | None = None,
) -> float:
    """Return how long Telegram should visibly show the persona typing."""
    rng = random.Random(seed)
    speed = _PERSONA_SPEED.get(persona, 1.0)
    jitter = 1.0 + rng.uniform(-0.18, 0.18)
    pace_factor = _PACE_MULTIPLIER.get(pace, 1.0)
    raw = (0.35 + len(reply) * _TYPE_PER_CHAR) * speed * pace_factor * jitter
    return max(0.7, min(14.0, raw))


def compute_delay(
    reply: str,
    persona: str,
    *,
    incoming_len: int = 0,
    pace: str = "normal",
    seed: int | None = None,
) -> float:
    """Return seconds to wait before sending `reply`. Pure / no sleep."""
    rng = random.Random(seed)
    speed = _PERSONA_SPEED.get(persona, 1.0)
    raw = _BASE + incoming_len * _READ_PER_CHAR + len(reply) * _TYPE_PER_CHAR
    jitter = 1.0 + rng.uniform(-_JITTER_FRAC, _JITTER_FRAC)
    pace_factor = _PACE_MULTIPLIER.get(pace, 1.0)
    delay = max(_MIN_DELAY, min(_MAX_DELAY, raw * speed * pace_factor * jitter))
    log.info(
        "L1 temporal: persona=%s reply_len=%d incoming_len=%d delay=%.1fs",
        persona,
        len(reply),
        incoming_len,
        delay,
    )
    return delay


def compute_followup_delay(
    reply: str,
    persona: str,
    *,
    pace: str = "normal",
    seed: int | None = None,
) -> float:
    """Return the shorter typing pause between consecutive message bubbles."""
    rng = random.Random(seed)
    speed = _PERSONA_SPEED.get(persona, 1.0)
    raw = 0.45 + len(reply) * 0.055
    jitter = 1.0 + rng.uniform(-_JITTER_FRAC, _JITTER_FRAC)
    pace_factor = _PACE_MULTIPLIER.get(pace, 1.0)
    return max(0.8, min(12.0, raw * speed * pace_factor * jitter))


async def wait(delay: float) -> None:
    """Async sleep for `delay` seconds (production send path)."""
    await asyncio.sleep(delay)
