"""Cost-tiering router (fyp.txt S6/S7 synergy, original contribution).

Default to the cheap model; escalate to the strong model only at moments that
move the needle. Because L1 already injects a human tarpit delay, the extra
latency of the strong model is hidden inside the delay we were adding anyway.

Every route decision is logged and should be appended to the session signal
trail so the S9 evaluation can measure cost-per-session and whether escalation
improves extraction/evasion.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hive.logging_setup import get_logger

log = get_logger(__name__)


class Tier(StrEnum):
    CHEAP = "cheap"    # ordinary persona chatter (most turns)
    STRONG = "strong"  # cover-critical / payoff turns
    LIGHT = "light"    # verdict soft-signal classification


@dataclass
class RouteInputs:
    """Signals the policy reads. Kept explicit so the decision is testable."""

    injection_flagged: bool = False   # S7 guardrail tripped (bot challenge, etc.)
    eliciting_hvi: bool = False       # agent is actively pushing for a HVI
    consistency_risk: bool = False    # drift / contradiction vs memory


def route(inputs: RouteInputs) -> Tier:
    """Pick the model tier for this turn and log the reason."""
    if inputs.injection_flagged:
        reason = "injection_flagged"
        tier = Tier.STRONG
    elif inputs.eliciting_hvi:
        reason = "eliciting_hvi"
        tier = Tier.STRONG
    elif inputs.consistency_risk:
        reason = "consistency_risk"
        tier = Tier.STRONG
    else:
        reason = "default"
        tier = Tier.CHEAP
    log.info("route decision: tier=%s reason=%s", tier.value, reason)
    return tier
