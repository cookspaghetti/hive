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

from hive.logging_setup import get_logger
from hive.state import SessionState, Verdict

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


def _collect_weights(
    session: SessionState,
    soft: dict[str, float] | None,
) -> list[tuple[str, float]]:
    """Return (reason, weight) contributions for the current state."""
    contribs: list[tuple[str, float]] = []
    for h in session.hvis:
        w = HARD_WEIGHTS.get(h.kind, _HARD_DEFAULT) * h.confidence
        contribs.append((f"hvi:{h.kind}", w))
    for res in session.sandbox_results:
        if res.get("verdict_signal") == "malicious":
            contribs.append(("sandbox:malicious", _SANDBOX_MALICIOUS))
    if soft:
        for label, conf in soft.items():
            w = SOFT_WEIGHTS.get(label, 0.1) * conf
            contribs.append((f"soft:{label}", w))
    return contribs


def _map_verdict(score: float) -> Verdict:
    if score >= SCAM_THRESHOLD:
        return "likely_scam"
    if score <= BENIGN_THRESHOLD:
        return "likely_benign"
    return "inconclusive"


def update_verdict(session: SessionState, soft: dict[str, float] | None = None) -> Verdict:
    """Recompute the verdict from accumulated signals; update the session.

    `soft` is the soft-signal classifier output (see classifier.py), injected
    so this function stays pure and offline-testable.
    """
    contribs = _collect_weights(session, soft)
    instantaneous_score = _noisy_or([w for _, w in contribs])
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
            "contributions": [{"reason": r, "weight": round(w, 4)} for r, w in contribs],
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
