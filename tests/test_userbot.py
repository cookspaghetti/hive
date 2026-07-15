"""Tests for UserbotTransport hand-back behaviour (review fix P1).

Verifies that a benign early-exit ends the takeover (peer removed from
_sessions) and fires the on_handback callback. Uses a fake engine so no
Telegram/LLM is needed; on_message never touches the Telethon client on the
hand-back path.
"""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

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
        return TurnOutput(
            text=None if self._handed_back else "hi",
            handed_back=self._handed_back,
            delay_s=2.0,
        )


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


def test_observed_incoming_chats_are_listed_without_starting_takeover():
    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=True))

    ub.observe_incoming(999, "first", 1, 10.0, "Alice", "alice")
    ub.observe_incoming(999, "latest", 2, 20.0)

    assert 999 not in ub._sessions
    assert ub.list_observed_chats() == [
        {
            "peer_id": 999,
            "name": "Alice",
            "username": "alice",
            "last_message": "latest",
            "last_message_id": 2,
            "last_message_at": 20.0,
            "message_count": 2,
            "active": False,
        }
    ]

    ub.begin_takeover(999, "confused_elderly")
    assert ub.list_observed_chats()[0]["active"] is True


def test_recent_inbound_dialogs_seed_observed_chats():
    inbound = SimpleNamespace(
        id=11,
        out=False,
        raw_text="existing inbound message",
        date=datetime.now(UTC),
    )
    outgoing = SimpleNamespace(
        id=12,
        out=True,
        raw_text="host reply",
        date=datetime.now(UTC),
    )
    dialogs = [
        SimpleNamespace(
            id=123,
            name="Existing sender",
            is_user=True,
            entity=SimpleNamespace(
                username="existing", bot=False, support=False, is_self=False
            ),
            message=inbound,
        ),
        SimpleNamespace(
            id=456,
            name="Outgoing chat",
            is_user=True,
            entity=SimpleNamespace(
                username="outgoing", bot=False, support=False, is_self=False
            ),
            message=outgoing,
        ),
        SimpleNamespace(
            id=789,
            name="Automated bot",
            is_user=True,
            entity=SimpleNamespace(
                username="automated_bot", bot=True, support=False, is_self=False
            ),
            message=inbound,
        ),
        SimpleNamespace(
            id=-100123,
            name="Group",
            is_user=False,
            entity=SimpleNamespace(
                username="group", bot=False, support=False, is_self=False
            ),
            message=inbound,
        ),
        SimpleNamespace(
            id=777000,
            name="Telegram",
            is_user=True,
            entity=SimpleNamespace(
                username="", bot=False, support=True, is_self=False
            ),
            message=inbound,
        ),
    ]

    class FakeClient:
        async def iter_dialogs(self, limit):
            assert limit == 50
            for dialog in dialogs:
                yield dialog

    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=True))
    ub._client = FakeClient()

    _run(ub._seed_recent_chats())

    chats = ub.list_observed_chats()
    assert [chat["peer_id"] for chat in chats] == [123]
    assert chats[0]["last_message"] == "existing inbound message"


def test_live_discovery_accepts_private_humans_and_rejects_bots_and_groups():
    ub = UserbotTransport(1, "hash", "sess", FakeEngine(handed_back=True))

    class FakeEvent:
        def __init__(self, *, private, bot=False):
            self.is_private = private
            self._sender = SimpleNamespace(
                first_name="Sender",
                last_name="",
                username="sender",
                bot=bot,
                support=False,
                is_self=False,
            )

        async def get_sender(self):
            return self._sender

    human = _run(ub._event_identity(FakeEvent(private=True)))
    bot = _run(ub._event_identity(FakeEvent(private=True, bot=True)))
    group = _run(ub._event_identity(FakeEvent(private=False)))

    assert human == ("Sender", "sender", True)
    assert bot[2] is False
    assert group[2] is False
