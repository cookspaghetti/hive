"""Prompt-injection screening (fyp.txt S7)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ScreenResult:
    flagged: bool
    reason: str = ""
    sanitized: str = ""


def screen(text: str) -> ScreenResult:
    """Screen an untrusted stranger message for injection attempts.

    TODO(S7): detect patterns like "ignore previous instructions", "are you a
    bot/AI", "what model are you"; neutralise, and signal the reason so the
    reasoning node can stay in persona rather than comply.
    """
    raise NotImplementedError
