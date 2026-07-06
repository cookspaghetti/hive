"""Temporal noise: the tarpit delay (fyp.txt "Tarpit Logic").

Computes a believable human delay before sending, scaled by reply length and
a random jitter, so response timing does not fingerprint as a bot.
"""

from __future__ import annotations


def compute_delay(reply: str, persona: str) -> float:
    """Return seconds to wait before sending `reply`.

    TODO(L1): model reading + typing time; add jitter; persona-specific pacing
    (e.g. elderly = slower). Measured by the detection-evasion metric (S9).
    """
    raise NotImplementedError
