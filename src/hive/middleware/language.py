"""Lightweight language-alignment checks for outbound persona replies.

The validator deliberately works at script level rather than translating or
rewriting a reply.  That keeps it deterministic, dependency-free, and safe for
the send path: a mismatch is made observable for evaluation and operations,
while the original persona response remains untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Language = Literal["english", "mandarin", "mixed", "unknown"]
AlignmentStatus = Literal["aligned", "mismatch", "uncertain"]

_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_LATIN_WORD_RE = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True)
class LanguageAlignment:
    """Script-level comparison between an incoming message and a reply."""

    expected: Language
    observed: Language
    status: AlignmentStatus
    reason: str


def _is_han(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x20000 <= codepoint <= 0x2FA1F
    )


def detect_language(text: str) -> Language:
    """Classify supported chat language from visible scripts.

    URLs are ignored because their Latin hostnames should not turn an otherwise
    Mandarin message into a mixed-language message. Short acronyms such as OTP
    and APK are treated similarly when surrounded by Mandarin.
    """
    visible = _URL_RE.sub(" ", text)
    han_count = sum(_is_han(char) for char in visible)
    latin_words = _LATIN_WORD_RE.findall(visible)
    latin_letters = sum(len(word) for word in latin_words)
    substantial_latin = latin_letters >= 8 and len(latin_words) >= 2

    if han_count >= 2:
        return "mixed" if substantial_latin else "mandarin"
    if latin_letters >= 2:
        # Manglish uses Latin script and is an accepted English-family mode.
        return "english"
    return "unknown"


def assess_language_alignment(incoming_text: str, reply_text: str) -> LanguageAlignment:
    """Assess alignment without modifying or translating either message."""
    expected = detect_language(incoming_text)
    observed = detect_language(reply_text)

    if expected == "unknown":
        return LanguageAlignment(
            expected,
            observed,
            "uncertain",
            "incoming message has insufficient English or Mandarin text",
        )
    if observed == "unknown":
        return LanguageAlignment(
            expected,
            observed,
            "uncertain",
            "reply has insufficient text for a reliable script check",
        )
    if expected == observed:
        return LanguageAlignment(expected, observed, "aligned", "scripts match")
    if expected == "mandarin" and observed == "mixed":
        return LanguageAlignment(
            expected,
            observed,
            "aligned",
            "Mandarin reply contains ordinary Latin terms or code-switching",
        )
    if expected == "mixed" and observed in {"english", "mandarin"}:
        return LanguageAlignment(
            expected,
            observed,
            "uncertain",
            "single-language reply to a code-switched message may be natural",
        )
    if expected == "english" and observed == "mixed":
        return LanguageAlignment(
            expected,
            observed,
            "uncertain",
            "reply code-switches away from an English-family message",
        )
    return LanguageAlignment(
        expected,
        observed,
        "mismatch",
        f"reply uses {observed} while the incoming message uses {expected}",
    )
