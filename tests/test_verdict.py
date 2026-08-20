"""S6 verdict engine tests (fyp.txt S6), fully offline."""

import time

from hive.state import HVI, Message, SessionState
from hive.verdict.classifier import _parse_assessment, _parse_scores, classify_soft
from hive.verdict.engine import _noisy_or, update_verdict
from tests.fakes import fake_client


def _session(**kw) -> SessionState:
    return SessionState(peer_id=1, persona="confused_elderly", **kw)


def test_noisy_or_bounds_and_monotonic():
    assert _noisy_or([]) == 0.0
    assert _noisy_or([1.0]) == 1.0
    assert 0.0 < _noisy_or([0.5]) < _noisy_or([0.5, 0.5]) < 1.0


def test_empty_session_is_benign():
    s = _session()
    assert update_verdict(s) == "likely_benign"
    assert s.verdict_score <= 0.2


def test_bank_account_plus_url_flags_scam():
    s = _session()
    s.hvis = [
        HVI(kind="bank_account", value="1234567890", source_msg_id=1, confidence=1.0),
        HVI(kind="url", value="http://phish.co", source_msg_id=1, confidence=1.0),
    ]
    assert update_verdict(s) == "likely_scam"
    assert s.verdict_score >= 0.7


def test_sandbox_malicious_is_near_decisive():
    s = _session()
    s.sandbox_results = [{"verdict_signal": "malicious"}]
    assert update_verdict(s) == "likely_scam"


def test_soft_signals_alone_can_reach_inconclusive():
    s = _session()
    v = update_verdict(s, soft={"urgency": 1.0, "investment_framing": 1.0})
    assert v in {"inconclusive", "likely_scam"}
    assert s.verdict_score > 0.2


def test_signal_trail_records_contributions():
    s = _session()
    source = Message("stranger", "visit http://x.co", time.time(), 1)
    s.messages.append(source)
    s.hvis = [HVI(kind="url", value="http://x.co", source_msg_id=1, confidence=1.0)]
    update_verdict(s)
    assert s.signal_trail
    last = s.signal_trail[-1]
    assert last["verdict"] == s.verdict
    assert any(c["reason"] == "hvi:url" for c in last["contributions"])
    assert last["source_message_ids"] == [1]
    assert last["contributions"][0]["source_message_ids"] == [1]


def test_signal_trail_omits_zero_weight_noise_and_records_batch_sources():
    s = _session()
    sources = [
        Message("stranger", "hello", time.time(), 7),
        Message("stranger", "pay today", time.time(), 8),
    ]

    update_verdict(
        s,
        soft={"urgency": 0.8, "authority_impersonation": 0.0},
        source_messages=sources,
    )

    last = s.signal_trail[-1]
    assert last["source_message_ids"] == [7, 8]
    assert [item["reason"] for item in last["contributions"]] == ["soft:urgency"]
    assert last["contributions"][0]["source_message_ids"] == [7, 8]


def test_scam_risk_does_not_regress_on_a_quieter_later_message():
    s = _session()
    assert update_verdict(
        s,
        soft={"payment_request": 1.0, "investment_framing": 1.0},
    ) == "likely_scam"
    peak = s.verdict_score

    assert update_verdict(s, soft={}) == "likely_scam"
    assert s.verdict_score == peak
    assert s.signal_trail[-1]["instantaneous_score"] == 0.0


def test_parse_scores_extracts_and_clamps():
    text = 'here: {"urgency": 0.9, "payment_request": 1.5, "nope": 3}'
    scores = _parse_scores(text)
    assert scores["urgency"] == 0.9
    assert scores["payment_request"] == 1.0  # clamped
    assert "nope" not in scores


def test_parse_scores_handles_garbage():
    assert _parse_scores("no json here") == {}


def test_structured_assessment_keeps_only_valid_message_evidence():
    assessment = _parse_assessment(
        """{"urgency":{"score":0.8,"message_ids":[7,999]},
        "payment_request":{"score":0.9,"message_ids":[999]}}""",
        {7},
    )

    assert assessment.scores["urgency"] == 0.8
    assert assessment.evidence["urgency"] == [7]
    assert assessment.scores["payment_request"] == 0.0


def test_classify_soft_with_fake_client():
    s = _session()
    s.messages.append(Message(role="stranger", text="urgent! pay now!", ts=time.time(), msg_id=0))
    client = fake_client(
        '{"urgency":{"score":0.8,"message_ids":[0]},'
        '"payment_request":{"score":0.7,"message_ids":[0]}}'
    )
    assessment = classify_soft(s, client)
    assert assessment.scores["urgency"] == 0.8
    assert assessment.evidence["urgency"] == [0]
