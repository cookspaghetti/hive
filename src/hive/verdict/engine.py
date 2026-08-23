"""Continuous hybrid verdict scoring (fyp.txt S6).

Runs every turn. Combines:
- Hard signals (rule-based, high trust): extracted HVIs and confirmed-malicious
  sandbox results. A phishing URL or a bank account is strong evidence; a
  confirmed-malicious sandbox result is near-decisive.
- Soft signals (LLM classifier, lower trust): urgency/pressure, authority
  impersonation, romance/investment framing, script inconsistency.

Signals are combined with a bounded noisy-OR so the score stays in [0, 1] and
multiple weak signals accumulate without ever exceeding 1. The score maps to
likely_scam / inconclusive / likely_benign via the thresholds below. Every
update appends to session.signal_trail for explainability and inclusion in the
Evidence Bundle. Original contribution — no framework ships scam scoring.
"""

from __future__ import annotations

import time
from typing import cast

from hive.logging_setup import get_logger
from hive.state import Message, SessionState, Verdict

log = get_logger(__name__)

SCAM_THRESHOLD = 0.7
BENIGN_THRESHOLD = 0.2

# Hard-signal weights by HVI kind (evidential strength that this is a scam).
HARD_WEIGHTS: dict[str, float] = {
    "url": 0.5,
    "bank_account": 0.6,
    "crypto_btc": 0.7,
    "crypto_eth": 0.7,
    "raw_qr": 0.4,
    "phone": 0.2,
    "phone_my": 0.2,
    "telegram_id": 0.25,
    "bank_name": 0.15,
    "person_name": 0.05,
    "organization": 0.05,
    "location": 0.03,
}
_HARD_DEFAULT = 0.2

# Sandbox result -> weight.
_SANDBOX_MALICIOUS = 0.9

# Soft-signal weights by classifier label.
SOFT_WEIGHTS: dict[str, float] = {
    "urgency": 0.3,
    "authority_impersonation": 0.4,
    "romance_framing": 0.35,
    "investment_framing": 0.4,
    "payment_request": 0.5,
    "inconsistency": 0.2,
}


def _noisy_or(weights: list[float]) -> float:
    """Combine independent evidence weights into a bounded [0,1] score."""
    p = 1.0
    for w in weights:
        p *= 1.0 - max(0.0, min(1.0, w))
    return 1.0 - p


def _collect_contributions(
    session: SessionState,
    soft: dict[str, float] | None,
    source_message_ids: list[int],
    soft_evidence: dict[str, list[int]] | None,
) -> list[dict[str, object]]:
    """Return scored contributions and the messages that support them."""
    contribs: list[dict[str, object]] = []
    for h in session.hvis:
        w = HARD_WEIGHTS.get(h.kind, _HARD_DEFAULT) * h.confidence
        if w > 0:
            contribs.append(
                {
                    "reason": f"hvi:{h.kind}",
                    "weight": w,
                    "source_message_ids": [h.source_msg_id],
                    "value": h.value,
                    "confidence": round(h.confidence, 4),
                    "extractor": h.extractor,
                    "scope": "new" if h.source_msg_id in source_message_ids else "carried",
                }
            )
    for res in session.sandbox_results:
        if res.get("verdict_signal") == "malicious":
            url = str(res.get("url") or "")
            url_sources = [
                h.source_msg_id
                for h in session.hvis
                if h.kind == "url" and (not url or h.value == url)
            ]
            contribs.append(
                {
                    "reason": "sandbox:malicious",
                    "weight": _SANDBOX_MALICIOUS,
                    "source_message_ids": url_sources,
                    "scope": (
                        "new"
                        if any(message_id in source_message_ids for message_id in url_sources)
                        else "carried"
                    ),
                }
            )
    if soft:
        for label, conf in soft.items():
            w = SOFT_WEIGHTS.get(label, 0.1) * conf
            if w > 0:
                evidence_ids = (
                    soft_evidence.get(label, [])
                    if soft_evidence is not None
                    else source_message_ids
                )
                contribs.append(
                    {
                        "reason": f"soft:{label}",
                        "weight": w,
                        "source_message_ids": evidence_ids,
                        "confidence": round(conf, 4),
                        "extractor": "llm_classifier",
                        "scope": (
                            "new"
                            if any(message_id in source_message_ids for message_id in evidence_ids)
                            else "context"
                        ),
                    }
                )
    return contribs


def _map_verdict(score: float) -> Verdict:
    if score >= SCAM_THRESHOLD:
        return "likely_scam"
    if score <= BENIGN_THRESHOLD:
        return "likely_benign"
    return "inconclusive"


def update_verdict(
    session: SessionState,
    soft: dict[str, float] | None = None,
    source_messages: list[Message] | None = None,
    soft_evidence: dict[str, list[int]] | None = None,
) -> Verdict:
    """Recompute the verdict from accumulated signals; update the session.

    `soft` is the soft-signal classifier output (see classifier.py), injected
    so this function stays pure and offline-testable.
    """
    if source_messages is None:
        source_messages = next(
            ([message] for message in reversed(session.messages) if message.role == "stranger"),
            [],
        )
    source_message_ids = list(dict.fromkeys(message.msg_id for message in source_messages))
    contribs = _collect_contributions(session, soft, source_message_ids, soft_evidence)
    instantaneous_score = _noisy_or([cast(float, item["weight"]) for item in contribs])
    # Scam evidence is cumulative. A later, less explicit message must not
    # erase a risk level already supported by the engagement record.
    score = max(session.verdict_score, instantaneous_score)
    verdict = _map_verdict(score)

    session.verdict_score = score
    session.verdict = verdict
    session.signal_trail.append(
        {
            "ts": time.time(),
            "turn": session.turn_count,
            "score": round(score, 4),
            "instantaneous_score": round(instantaneous_score, 4),
            "verdict": verdict,
            "source_message_ids": source_message_ids,
            "contributions": [
                {
                    **item,
                    "weight": round(cast(float, item["weight"]), 4),
                }
                for item in contribs
            ],
        }
    )
    log.info(
        "S6 verdict: turn=%d score=%.3f verdict=%s signals=%d",
        session.turn_count,
        score,
        verdict,
        len(contribs),
    )
    return verdict
