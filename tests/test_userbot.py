"""Tests for UserbotTransport hand-back behaviour (review fix P1).

Verifies that a benign early-exit ends the takeover (peer removed from
_sessions) and fires the on_handback callback. Uses a fake engine so no
Telegram/LLM is needed; on_message never touches the Telethon client on the
hand-back path.
"""

import asyncio

from hive.runtime import TurnOutput
from hive.transports.userbot import UserbotTransport


class FakeEngine:
    """Duck-typed engine: returns handed_back or a normal reply on demand."""

    def __init__(self, handed_back: bool):
        self._handed_back = handed_back
        self.closed = False

    def new_session(self, peer_id, persona):
        # minimal session object with the attributes the transport/callbacks use
        from hive.state import SessionState
        from hive.vault.hashchain import HashChain
        return SessionState(peer_id=peer_id, persona=persona), HashChain()

    def process_turn(self, session, chain, inbound):
        return TurnOutput(text=None if self._handed_back else "hi", handed_back=self._handed_back, delay_s=2.0)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_benign_handback_ends_takeover_and_notifies():
    fired = {}

    async def on_handback(peer_id, session):
        fired["peer"] = peer_id

    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=True), on_handback=on_handback)
    ub.begin_takeover(555, "confused_elderly")
    assert 555 in ub._sessions

    _run(ub.on_message(555, "ok bye thanks", 1, 0.0))

    assert 555 not in ub._sessions          # takeover ended
    assert fired.get("peer") == 555         # operator notified


def test_active_conversation_keeps_session():
    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=False))
    ub.begin_takeover(777, "naive_young_adult")
    # send_as_user would need a live client; monkeypatch it to a no-op
    async def _noop(peer_id, text):
        pass
    ub.send_as_user = _noop
    _run(ub.on_message(777, "tell me more", 1, 0.0))
    assert 777 in ub._sessions              # still under takeover


def test_on_message_ignores_unknown_peer():
    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=True))
    # no takeover started for this peer -> no error, no state
    _run(ub.on_message(999, "hello", 1, 0.0))
    assert 999 not in ub._sessions
