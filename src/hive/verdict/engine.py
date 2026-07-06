"""Continuous hybrid verdict scoring (fyp.txt S6).

Hard signals (rule-based): each HVI and a confirmed-malicious sandbox result
add weight. Soft signals (LLM classifier): urgency, authority impersonation,
romance/investment framing, script inconsistency. Aggregated into a score
mapped to likely_scam / inconclusive / likely_benign. The signal trail is
logged for explainability and included in the evidence bundle. Original work.
"""

from __future__ import annotations

from hive.state import SessionState, Verdict

SCAM_THRESHOLD = 0.7
BENIGN_THRESHOLD = 0.2


def update_verdict(session: SessionState) -> Verdict:
    """Recompute the verdict from accumulated signals and update the session.

    TODO(S6): combine hard-signal weights (from session.hvis / sandbox_results)
    with soft-signal LLM scores; append to session.signal_trail; set
    session.verdict_score and map to a Verdict.
    """
    raise NotImplementedError
