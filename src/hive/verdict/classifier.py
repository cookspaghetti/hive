"""Soft-signal LLM classifier for the Verdict Engine (fyp.txt S6).

Runs the LIGHT model tier over the recent conversation and returns a mapping of
soft-signal label -> confidence (0..1). Kept separate from the scorer so the
scorer stays pure/offline-testable and this piece can be swapped or mocked.

Runs every turn, so it deliberately uses the cheap/light tier (router.Tier.LIGHT).
"""

from __future__ import annotations

import json

from hive.llm.client import ChatMessage, LLMClient
from hive.llm.router import Tier
from hive.logging_setup import get_logger
from hive.state import SessionState
from hive.verdict.engine import SOFT_WEIGHTS

log = get_logger(__name__)

_HISTORY_WINDOW = 10

_SYSTEM = (
    "You are a fraud-signal classifier for an anti-scam system. Read the chat"
    " and rate, from 0.0 to 1.0, how strongly each of these signals is present"
    " in the OTHER party's messages:\n"
    + ", ".join(SOFT_WEIGHTS.keys())
    + ".\nReturn ONLY a compact JSON object mapping each label to its score."
    " No prose."
)


def _parse_scores(text: str) -> dict[str, float]:
    """Best-effort parse of the model's JSON; ignore junk, clamp to [0,1]."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        raw = json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        log.warning("S6 classifier: could not parse JSON from model output")
        return {}
    out: dict[str, float] = {}
    for label in SOFT_WEIGHTS:
        val = raw.get(label)
        if isinstance(val, (int, float)):
            out[label] = max(0.0, min(1.0, float(val)))
    return out


def classify_soft(session: SessionState, client: LLMClient) -> dict[str, float]:
    """Return soft-signal scores for the current conversation."""
    convo = "\n".join(
        f"{'THEM' if m.role == 'stranger' else 'ME'}: {m.text}"
        for m in session.messages[-_HISTORY_WINDOW:]
    )
    messages = [
        ChatMessage(role="system", content=_SYSTEM),
        ChatMessage(role="user", content=convo),
    ]
    resp = client.complete(messages, tier=Tier.LIGHT, temperature=0.0)
    scores = _parse_scores(resp.text)
    log.info("S6 classifier: parsed %d soft signals", len(scores))
    return scores
