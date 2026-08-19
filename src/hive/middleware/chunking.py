"""Turn one model reply into a bounded, natural Telegram reply plan."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, cast

_EXPLICIT_BREAK = re.compile(r"\s*(?:\|\|\||\n\s*\n)\s*")
_PACE_DIRECTIVE = re.compile(
    r"(?:\[\[?|<)\s*pace\s*[:=]\s*(fast|normal|slow)\s*(?:\]\]?|>)",
    re.IGNORECASE,
)
_FORMAT_PREFIX = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s+")
_SOFT_BREAKS = ",;，；、 "
Pace = Literal["fast", "normal", "slow"]


@dataclass(frozen=True, slots=True)
class ReplyPlan:
    messages: tuple[str, ...]
    pace: Pace = "normal"


def _clean(text: str) -> str:
    lines = [_FORMAT_PREFIX.sub("", line) for line in text.splitlines()]
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


def _sentences(text: str) -> list[str]:
    """Split on real sentence endings without breaking dots inside URLs."""
    rows: list[str] = []
    start = 0
    for index, character in enumerate(text):
        next_character = text[index + 1] if index + 1 < len(text) else ""
        cjk_end = character in "。！？"
        latin_end = character in ".!?" and (not next_character or next_character.isspace())
        if cjk_end or latin_end:
            value = _clean(text[start : index + 1])
            if value:
                rows.append(value)
            start = index + 1
    tail = _clean(text[start:])
    if tail:
        rows.append(tail)
    return rows


def _split_long(value: str, limit: int) -> list[str]:
    rows: list[str] = []
    remaining = value
    while len(remaining) > limit:
        floor = max(1, int(limit * 0.55))
        candidates = [remaining.rfind(mark, floor, limit + 1) for mark in _SOFT_BREAKS]
        split_at = max(candidates)
        if split_at < floor:
            split_at = limit
        elif remaining[split_at] not in " ":
            split_at += 1
        rows.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        rows.append(remaining)
    return rows


def _cap_bubbles(
    rows: list[str],
    maximum: int,
    max_total_chars: int,
) -> tuple[str, ...]:
    selected = rows[:maximum]
    # If verbosity has to be cut, retain the final elicitation question rather
    # than cramming every discarded thought into an oversized last bubble.
    final_question = (
        next(
            (row for row in reversed(rows[maximum:]) if "?" in row or "？" in row),
            None,
        )
        if len(rows) > maximum
        else None
    )
    if final_question is not None:
        selected[-1] = final_question
    # Three concise thoughts are fine; three long ones still read like an LLM
    # paragraph spread across bubbles. Preserve reaction + final question.
    while len(selected) > 2 and sum(map(len, selected)) > max_total_chars:
        selected.pop(-2)
    return tuple(selected)


def plan_reply(
    text: str,
    *,
    target_chars: int = 82,
    max_chars: int = 96,
    max_bubbles: int = 3,
    max_total_chars: int = 160,
) -> ReplyPlan:
    """Parse pace metadata and create one to three sendable thought bubbles."""
    match = _PACE_DIRECTIVE.search(text)
    pace = cast(Pace, match.group(1).lower()) if match else "normal"
    cleaned = _PACE_DIRECTIVE.sub("", text).strip()
    if not cleaned:
        return ReplyPlan((), pace)

    explicit = [_clean(row) for row in _EXPLICIT_BREAK.split(cleaned) if _clean(row)]
    if len(explicit) > 1:
        units = [unit for row in explicit for unit in _split_long(row, max_chars)]
        return ReplyPlan(_cap_bubbles(units, max_bubbles, max_total_chars), pace)
    if len(explicit[0]) <= max_chars:
        return ReplyPlan((explicit[0],), pace)

    units: list[str] = []
    for sentence in _sentences(explicit[0]):
        units.extend(_split_long(sentence, max_chars))

    bubbles: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current} {unit}".strip()
        if current and len(candidate) > target_chars:
            bubbles.append(current)
            current = unit
        else:
            current = candidate
    if current:
        bubbles.append(current)
    return ReplyPlan(_cap_bubbles(bubbles, max_bubbles, max_total_chars), pace)


def split_reply(
    text: str,
    *,
    target_chars: int = 82,
    max_chars: int = 96,
    max_bubbles: int = 3,
    max_total_chars: int = 160,
) -> tuple[str, ...]:
    """Return one to three short bubbles, respecting explicit ``|||`` breaks."""
    return plan_reply(
        text,
        target_chars=target_chars,
        max_chars=max_chars,
        max_bubbles=max_bubbles,
        max_total_chars=max_total_chars,
    ).messages
