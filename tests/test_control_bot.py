"""Tests for ControlBot argument validation (review fix P2).

Verifies that malformed/missing command args produce a usage reply instead of
raising, and that unknown personas are rejected. Uses fake update/context
objects; no live Telegram.
"""

import asyncio
from dataclasses import dataclass

from hive.history import TakeoverHistoryStore
from hive.state import Message, SessionState
from hive.transports.control_bot import HELP_TEXT, ControlBot
from hive.transports.userbot import UserbotTransport


@dataclass
class FakeSettings:
    operator_id: int = 42
    default_persona: str = "confused_elderly"
    control_bot_token: str = ""
    signing_key_path: str = "k.pem"


class FakeEngine:
    def new_session(self, peer_id, persona):
        from hive.state import SessionState
        from hive.vault.hashchain import HashChain
        return SessionState(peer_id=peer_id, persona=persona), HashChain()

    def summary(self, session):
        return "summary"


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, uid=42):
        self.effective_user = FakeUser(uid)
        self.message = FakeMessage()


class FakeContext:
    def __init__(self, args):
        self.args = args


def _bot(history_store=None):
    ub = UserbotTransport(1, "h", "s", FakeEngine())
    return ControlBot(FakeSettings(), FakeEngine(), ub, history_store=history_store)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_takeover_missing_args_replies_usage():
    bot = _bot()
    up = FakeUpdate()
    _run(bot._cmd_takeover(up, FakeContext([])))
    assert up.message.replies and "Usage" in up.message.replies[-1]


def test_takeover_non_int_peer_replies_usage():
    bot = _bot()
    up = FakeUpdate()
    _run(bot._cmd_takeover(up, FakeContext(["notanumber"])))
    assert "Usage" in up.message.replies[-1]


def test_takeover_unknown_persona_rejected():
    bot = _bot()
    up = FakeUpdate()
    _run(bot._cmd_takeover(up, FakeContext(["555", "wizard"])))
    assert "Unknown persona" in up.message.replies[-1]


def test_takeover_valid_starts():
    bot = _bot()
    up = FakeUpdate()
    _run(bot._cmd_takeover(up, FakeContext(["555", "confused_elderly"])))
    assert 555 in bot.userbot._sessions


def test_unauthorised_user_rejected():
    bot = _bot()
    up = FakeUpdate(uid=999)  # not the operator
    _run(bot._cmd_takeover(up, FakeContext(["555"])))
    assert "Unauthorised" in up.message.replies[-1]
    assert 555 not in bot.userbot._sessions


def test_persona_missing_arg_replies_usage():
    bot = _bot()
    up = FakeUpdate()
    _run(bot._cmd_persona(up, FakeContext([])))
    assert "Usage" in up.message.replies[-1]


def test_start_and_help_reply_with_command_reference():
    bot = _bot()
    up = FakeUpdate()

    _run(bot._cmd_help(up, FakeContext([])))

    assert up.message.replies[-1] == HELP_TEXT
    assert "/chats" in up.message.replies[-1]


def test_plain_text_fallback_replies_instead_of_silently_ignoring():
    bot = _bot()
    up = FakeUpdate()

    _run(bot._cmd_fallback(up, FakeContext([])))

    assert "Use one of the commands" in up.message.replies[-1]


def test_chats_lists_observed_private_peers():
    bot = _bot()
    up = FakeUpdate()
    bot.userbot.observe_incoming(555, "hello", 1, 1.0, "Sender", "sender")

    _run(bot._cmd_chats(up, FakeContext([])))

    assert "555: Sender (@sender) [available]" in up.message.replies[-1]


def test_help_still_rejects_unauthorised_users():
    bot = _bot()
    up = FakeUpdate(uid=999)

    _run(bot._cmd_help(up, FakeContext([])))

    assert up.message.replies[-1] == "Unauthorised."


def test_handback_archives_takeover_history(tmp_path):
    store = TakeoverHistoryStore(tmp_path)
    bot = _bot(store)
    session = SessionState(peer_id=555, persona="confused_elderly")
    session.messages.append(Message("stranger", "hello", 1.0, 1))

    _run(bot._on_handback(555, session))

    assert store.list()[0]["peer_id"] == 555
    assert store.list()[0]["message_count"] == 1
