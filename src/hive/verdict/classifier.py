"""Soft-signal LLM classifier for the Verdict Engine (fyp.txt S6).

Runs the LIGHT model tier over the recent conversation and returns a mapping of
soft-signal label -> confidence (0..1). Kept separate from the scorer so the
scorer stays pure/offline-testable and this piece can be swapped or mocked.

Runs every turn, so it deliberately uses the cheap/light tier (router.Tier.LIGHT).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from hive.llm.client import ChatMessage, LLMClient
from hive.llm.router import Tier
from hive.logging_setup import get_logger
from hive.state import SessionState
from hive.verdict.engine import SOFT_WEIGHTS

log = get_logger(__name__)

_HISTORY_WINDOW = 10

_SYSTEM = (
    "You are a fraud-signal classifier for an anti-scam system. Read the chat "
    "and rate, from 0.0 to 1.0, how strongly each signal is present in the OTHER "
    "party's messages:\n"
    + ", ".join(SOFT_WEIGHTS.keys())
    + ".\nReturn ONLY one compact JSON object. Map every label to an object with "
    '"score" and "message_ids". message_ids must cite only THEM message IDs that '
    "directly support that signal. A positive score requires at least one cited "
    'message. Example: {"urgency":{"score":0.8,"message_ids":[123]}}. No prose.'
)


@dataclass(frozen=True)
class SoftAssessment:
    scores: dict[str, float]
    evidence: dict[str, list[int]]


def _json_object(text: str) -> dict:
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        raw = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        log.warning("S6 classifier: could not parse JSON from model output")
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_assessment(
    text: str,
    valid_message_ids: set[int] | None = None,
) -> SoftAssessment:
    """Parse scores plus verifiable message-level evidence.

    Numeric values remain accepted for compatibility with older model output,
    but structured positive signals are rejected when none of their cited
    message IDs exist in the supplied classifier window.
    """
    raw = _json_object(text)
    scores: dict[str, float] = {}
    evidence: dict[str, list[int]] = {}
    for label in SOFT_WEIGHTS:
        value = raw.get(label)
        ids: list[int] = []
        if isinstance(value, dict):
            score = value.get("score")
            raw_ids = value.get("message_ids")
            if isinstance(raw_ids, list):
                ids = list(
                    dict.fromkeys(
                        item
                        for item in raw_ids
                        if isinstance(item, int)
                        and (valid_message_ids is None or item in valid_message_ids)
                    )
                )
            if valid_message_ids is not None and isinstance(score, (int, float)) and score > 0:
                if not ids:
                    score = 0.0
        else:
            score = value
        if isinstance(score, (int, float)):
            scores[label] = max(0.0, min(1.0, float(score)))
            evidence[label] = ids
    return SoftAssessment(scores=scores, evidence=evidence)


def _parse_scores(text: str) -> dict[str, float]:
    """Best-effort parse of the model's JSON; ignore junk, clamp to [0,1]."""
    return _parse_assessment(text).scores


def classify_soft(session: SessionState, client: LLMClient) -> SoftAssessment:
    """Return soft-signal scores with validated message-level evidence."""
    window = session.messages[-_HISTORY_WINDOW:]
    convo = "\n".join(
        f"{'THEM' if m.role == 'stranger' else 'ME'} [{m.msg_id}]: {m.text}"
        for m in window
    )
    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(role="user", content=convo),
    ]
    resp = client.complete(messages, tier=Tier.LIGHT, temperature=0.0)
    assessment = _parse_assessment(
        resp.text,
        {message.msg_id for message in window if message.role == "stranger"},
    )
    log.info("S6 classifier: parsed %d soft signals", len(assessment.scores))
    return assessment
