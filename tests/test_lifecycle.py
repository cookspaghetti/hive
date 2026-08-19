"""LangGraph turn-pipeline tests (fyp.txt S8).

Exercises the real compiled graph via HiveEngine.process_turn with fakes:
a normal turn runs end-to-end (reason + middleware produce a reply), and the
budget nodes terminate the session on turn/duration limits.
"""

import time

import pytest

from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.state import Message
from tests.fakes import fake_client


class _CleanRunner:
    def run(self, url):
        return RawFindings(final_url=url, body_len=1000, title="ok")


def _engine(**kw):
    return HiveEngine(agent_client=fake_client("ok lah"), sandbox_runner=_CleanRunner(),
                      enable_early_exit=False, **kw)


def test_normal_turn_produces_reply():
    eng = _engine()
    session, chain = eng.new_session(peer_id=1, persona="confused_elderly")
    out = eng.process_turn(session, chain, Message("stranger", "hello there", time.time(), 0))
    # L1 middleware may add noise, so don't assert verbatim equality.
    assert out.text and isinstance(out.text, str)
    assert out.delay_s >= 2.0
    assert not out.terminated and not out.handed_back
    assert len(chain.entries) >= 2  # msg_in + msg_out


def test_burst_preserves_each_inbound_and_records_each_reply_bubble():
    eng = HiveEngine(
        agent_client=fake_client("wait ah ||| which bank account?"),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=False,
    )
    session, chain = eng.new_session(peer_id=8, persona="confused_elderly")
    out = eng.process_messages(
        session,
        chain,
        [
            Message("stranger", "hello", time.time(), 10),
            Message("stranger", "transfer today", time.time(), 11),
        ],
    )

    assert [message.text for message in session.messages[:2]] == ["hello", "transfer today"]
    assert len(out.messages) == 2
    assert [message.text for message in session.messages[-2:]] == list(out.messages)
    assert session.turn_count == 2
    assert session.exchange_count == 1
    assert [message.msg_id for message in session.messages[-2:]] == [-1, -2]


def test_transport_can_defer_outbound_evidence_until_delivery():
    eng = HiveEngine(
        agent_client=fake_client("[[pace:fast]] wait ah ||| which account?"),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=False,
    )
    session, chain = eng.new_session(peer_id=81, persona="confused_elderly")

    out = eng.process_messages(
        session,
        chain,
        [Message("stranger", "transfer today", time.time(), 10)],
        record_outbound=False,
    )

    assert [message.role for message in session.messages] == ["stranger"]
    assert not any(entry.payload["event"] == "msg_out" for entry in chain.entries)
    eng.record_outbound(session, chain, out.messages[0], ts=200.0)
    assert session.messages[-1].role == "agent"
    assert session.messages[-1].ts == 200.0
    assert chain.entries[-1].payload["event"] == "msg_out"


def test_light_model_can_steer_a_pending_reply_before_new_messages():
    eng = HiveEngine(
        agent_client=fake_client("SEND_FIRST"),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=False,
    )
    session, _chain = eng.new_session(peer_id=82, persona="naive_young_adult")

    decision = eng.steer_pending_reply(
        session,
        "wait ah",
        [Message("stranger", "also send it today", time.time(), 11)],
    )

    assert decision == "send_first"


def test_burst_does_not_trigger_benign_handback_as_three_exchanges():
    eng = HiveEngine(
        agent_client=fake_client("ok"),
        sandbox_runner=_CleanRunner(),
        enable_early_exit=True,
        early_exit_min_turns=3,
    )
    session, chain = eng.new_session(peer_id=9, persona="naive_young_adult")

    out = eng.process_messages(
        session,
        chain,
        [
            Message("stranger", "bro", time.time(), 1),
            Message("stranger", "you awake?", time.time(), 2),
            Message("stranger", "I am John", time.time(), 3),
        ],
    )

    assert out.handed_back is False
    assert out.text
    assert session.turn_count == 3
    assert session.exchange_count == 1


def test_max_turns_budget_terminates():
    eng = _engine(max_turns=2)
    session, chain = eng.new_session(peer_id=2, persona="naive_young_adult")
    for i in range(2):  # turns 1 and 2 are within budget
        out = eng.process_turn(session, chain, Message("stranger", f"msg {i}", time.time(), i))
        assert out.text is not None
    # turn 3 exceeds max_turns -> terminate
    out = eng.process_turn(session, chain, Message("stranger", "again", time.time(), 3))
    assert out.terminated is True
    assert out.reason == "max_turns"
    assert out.text is None


def test_max_duration_budget_terminates():
    eng = _engine(max_turns=0, max_session_minutes=30)
    session, chain = eng.new_session(peer_id=3, persona="overseas_worker")
    session.started_ts = time.time() - 31 * 60  # pretend the session started 31 min ago
    out = eng.process_turn(session, chain, Message("stranger", "still there?", time.time(), 0))
    assert out.terminated is True
    assert out.reason == "max_duration"


def test_budget_disabled_when_zero():
    eng = _engine(max_turns=0, max_session_minutes=0)
    session, chain = eng.new_session(peer_id=4, persona="small_business_owner")
    for i in range(5):
        out = eng.process_turn(session, chain, Message("stranger", f"m{i}", time.time(), i))
    assert not out.terminated


def test_failed_seal_restores_active_phase(tmp_path, monkeypatch):
    from hive.state import Phase

    eng = _engine()
    session, chain = eng.new_session(peer_id=5, persona="small_business_owner")
    session.phase = Phase.ACTIVE

    def fail_bundle(*args, **kwargs):
        raise RuntimeError("font rejected")

    monkeypatch.setattr("hive.vault.bundle.build_bundle", fail_bundle)

    with pytest.raises(RuntimeError, match="font rejected"):
        eng.close_session(session, chain, str(tmp_path / "case.pdf"), "key.pem")

    assert session.phase is Phase.ACTIVE
