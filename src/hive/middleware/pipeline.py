"""L1 middleware entry point (fyp.txt L1).

Combines linguistic + temporal noise into a single post-processing step that
sits between the L2 reason node and the transport send. Returns the noised
text and the delay to apply; the caller (send path) performs the actual wait
so this stays synchronous and testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from hive.middleware.chunking import Pace, plan_reply
from hive.middleware.linguistic import has_manglish_particle, inject_noise
from hive.middleware.temporal import (
    calibrate_pace,
    compute_delay,
    compute_followup_delay,
    compute_typing_duration,
)


@dataclass
class MiddlewareResult:
    text: str      # noised reply, joined for backward-compatible callers
    delay_s: float  # seconds to wait before sending
    messages: tuple[str, ...] = ()
    message_delays_s: tuple[float, ...] = ()
    message_typing_s: tuple[float, ...] = ()
    pace: Pace = "normal"


def apply(
    raw_reply: str,
    persona: str,
    *,
    incoming_len: int = 0,
    incoming_text: str = "",
    seed: int | None = None,
) -> MiddlewareResult:
    """Apply human-emulation noise to a raw agent reply."""
    plan = plan_reply(raw_reply)
    raw_messages = plan.messages
    pace = calibrate_pace(
        plan.pace,
        incoming_text=incoming_text,
        reply_text=raw_reply,
    )
    existing_particle = any(has_manglish_particle(message) for message in raw_messages)
    messages = tuple(
        inject_noise(
            message,
            persona,
            manglish=not existing_particle and index == len(raw_messages) - 1,
            seed=None if seed is None else seed + index,
        )
        for index, message in enumerate(raw_messages)
    )
    if not messages:
        return MiddlewareResult(text="", delay_s=0.0)
    delays = (
        compute_delay(
            messages[0],
            persona,
            incoming_len=incoming_len,
            pace=pace,
            seed=seed,
        ),
        *(
            compute_followup_delay(
                message,
                persona,
                pace=pace,
                seed=None if seed is None else seed + index,
            )
            for index, message in enumerate(messages[1:], start=1)
        ),
    )
    typing = tuple(
        min(
            delays[index],
            compute_typing_duration(
                message,
                persona,
                pace=pace,
                seed=None if seed is None else seed + 100 + index,
            ),
        )
        for index, message in enumerate(messages)
    )
    return MiddlewareResult(
        text="\n\n".join(messages),
        delay_s=delays[0],
        messages=messages,
        message_delays_s=delays,
        message_typing_s=typing,
        pace=cast(Pace, pace),
    )
