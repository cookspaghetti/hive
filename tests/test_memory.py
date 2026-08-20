"""L2 memory tests (fyp.txt L2): KeywordMemory + engine recall wiring."""

import time

from hive.agent.memory import KeywordMemory, NullMemory
from hive.llm.client import LLMClient
from hive.llm.router import Tier
from hive.runtime import HiveEngine
from hive.sandbox.runner import RawFindings
from hive.state import Message
from tests.fakes import FakeBackend

# --- KeywordMemory unit behaviour ---

def test_keyword_memory_recalls_prior_stranger_detail():
    m = KeywordMemory(peer_id=1)
    m.add("stranger", "my Maybank account is 1234567890")
    m.add("agent", "ok ah")
    hits = m.recall("which account should i transfer to")
    assert any("1234567890" in h for h in hits)


def test_keyword_memory_ignores_agent_messages():
    m = KeywordMemory(peer_id=1)
    m.add("agent", "please give me your account number")
    assert m.recall("account number") == []


def test_keyword_memory_no_match_returns_empty():
    m = KeywordMemory(peer_id=1)
    m.add("stranger", "hello there friend")
    assert m.recall("cryptocurrency wallet address") == []


def test_null_memory_recalls_nothing():
    m = NullMemory()
    m.add("stranger", "anything")
    assert m.recall("anything") == []


# --- engine wiring: earlier disclosure resurfaces in a later prompt ---

class _CleanRunner:
    def run(self, url):
        return RawFindings(final_url=url, body_len=1000, title="ok")


def _capturing_engine():
    backend = FakeBackend()  # canned "{}" not needed; classifier returns {} on non-json
    client = LLMClient(backend, {Tier.CHEAP: "c", Tier.STRONG: "s", Tier.LIGHT: "l"})
    eng = HiveEngine(agent_client=client, sandbox_runner=_CleanRunner(), enable_early_exit=False)
    return eng, backend


def test_engine_feeds_recall_into_reason_prompt():
    eng, backend = _capturing_engine()
    session, chain = eng.new_session(peer_id=555, persona="confused_elderly")

    # Turn 1: scammer discloses a Telegram handle.
    eng.process_turn(
        session,
        chain,
        Message("stranger", "contact me at @scammerboss on telegram", time.time(), 0),
    )
    # Turn 2: a later message that shares the 'telegram' keyword should recall it.
    eng.process_turn(
        session,
        chain,
        Message("stranger", "did you add me on telegram yet?", time.time(), 2),
    )

    # The system prompt of the LAST reason call should contain the recalled detail.
    system_prompt = backend.calls[-1]["messages"][0]["content"]
    assert "@scammerboss" in system_prompt
    assert "told you so far" in system_prompt  # recall section header


def test_forget_clears_memory():
    eng, _ = _capturing_engine()
    eng.new_session(peer_id=7, persona="naive_young_adult")
    assert 7 in eng._memories
    eng.forget(7)
    assert 7 not in eng._memories
