"""LangGraph turn-pipeline tests (fyp.txt S8).

Exercises the real compiled graph via HiveEngine.process_turn with fakes:
a normal turn runs end-to-end (reason + middleware produce a reply), and the
budget nodes terminate the session on turn/duration limits.
"""

import time

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
