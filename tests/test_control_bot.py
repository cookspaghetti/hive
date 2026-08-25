"""Tests for ControlBot argument validation (review fix P2).

Verifies that malformed/missing command args produce a usage reply instead of
raising, and that unknown personas are rejected. Uses fake update/context
objects; no live Telegram.
"""

import asyncio
from dataclasses import dataclass

from hive.history import TakeoverHistoryStore
from hive.runtime import TurnOutput
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
    def __init__(self):
        self.fail_seal = False

    def new_session(self, peer_id, persona):
        from hive.state import SessionState
        from hive.vault.hashchain import HashChain
        return SessionState(peer_id=peer_id, persona=persona), HashChain()

    def summary(self, session):
        return "summary"

    def process_turn(self, session, chain, inbound):
        session.messages.append(inbound)
        session.turn_count += 1
        session.exchange_count += 1
        chain.append({"event": "msg_in", "msg_id": inbound.msg_id}, ts=inbound.ts)
        return TurnOutput(text=None)

    def close_session(self, session, chain, out_path, key_path, operator_name=""):
        if self.fail_seal:
            raise RuntimeError("renderer failed")
        from pathlib import Path

        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"pdf")
        return path


class FakeMessage:
    def __init__(self):
        self.replies = []
        self.reply_markups = []
        self.documents = []

    async def reply_text(self, text, reply_markup=None):
        self.replies.append(text)
        self.reply_markups.append(reply_markup)

    async def reply_document(self, document, filename):
        self.documents.append((filename, document.read()))


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


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.answers = []
        self.edits = []
        self.edit_markups = []
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=False):
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_text(self, text, reply_markup=None):
        self.edits.append(text)
        self.edit_markups.append(reply_markup)


class FakeCallbackUpdate:
    def __init__(self, data, uid=42):
        self.effective_user = FakeUser(uid)
        self.callback_query = FakeCallbackQuery(data)


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


def test_persona_without_args_shows_persona_buttons():
    bot = _bot()
    up = FakeUpdate()

    _run(bot._cmd_persona(up, FakeContext([])))

    assert "Choose the default persona" in up.message.replies[-1]
    buttons = [
        button
        for row in up.message.reply_markups[-1].inline_keyboard
        for button in row
    ]
    assert any(button.text == "✓ Confused Elderly" for button in buttons)
    assert any(
        button.callback_data == "hive_persona:set:small_business_owner"
        for button in buttons
    )


def test_persona_button_changes_default_persona():
    bot = _bot()
    update = FakeCallbackUpdate("hive_persona:set:small_business_owner")

    _run(bot._callback_persona(update, FakeContext([])))

    assert bot.default_persona == "small_business_owner"
    assert "Small Business Owner" in update.callback_query.edits[-1]


def test_typed_persona_still_sets_default_for_compatibility():
    bot = _bot()
    up = FakeUpdate()

    _run(bot._cmd_persona(up, FakeContext(["small_business_owner"])))

    assert bot.default_persona == "small_business_owner"


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


def test_handback_notifies_operator_bot_that_manual_control_resumed(tmp_path):
    sent = []

    class TelegramBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    class App:
        bot = TelegramBot()

    bot = _bot(TakeoverHistoryStore(tmp_path))
    bot._app = App()
    session = SessionState(peer_id=555, persona="confused_elderly")
    session.peer_display_name = "Sender"
    session.peer_username = "sender"
    session.verdict = "likely_benign"
    session.exchange_count = 10
    session.turn_count = 20

    _run(bot._on_handback(555, session))

    assert sent[0]["chat_id"] == 42
    assert "HIVE handed the chat back" in sent[0]["text"]
    assert "Sender (@sender)" in sent[0]["text"]
    assert "Analysed exchanges: 10" in sent[0]["text"]
    assert "HIVE has stopped replying" in sent[0]["text"]
    assert "continue the conversation manually" in sent[0]["text"]


def test_takeover_request_is_pushed_to_operator_bot():
    sent = []

    class TelegramBot:
        async def send_message(self, **kwargs):
            sent.append(kwargs)

    class App:
        bot = TelegramBot()

    bot = _bot()
    bot._app = App()
    delivered = _run(
        bot._on_takeover_request(
            {
                "peer_id": 555,
                "name": "Sender",
                "username": "sender",
                "last_message": "please reply",
                "message_count": 2,
            }
        )
    )

    assert delivered is True
    assert sent[0]["chat_id"] == 42
    assert "HIVE takeover request" in sent[0]["text"]
    assert "Sender (@sender)" in sent[0]["text"]
    buttons = sent[0]["reply_markup"].inline_keyboard[0]
    assert [button.text for button in buttons] == ["Yes — Take over", "No — Dismiss"]
    assert buttons[0].callback_data == "hive_takeover:yes:555"
    assert buttons[1].callback_data == "hive_takeover:no:555"


def test_yes_button_starts_takeover_and_resolves_request():
    bot = _bot()
    bot.userbot.observe_incoming(555, "hello", 1, 1.0, "Sender", "sender")
    update = FakeCallbackUpdate("hive_takeover:yes:555")

    _run(bot._callback_takeover_request(update, FakeContext([])))

    assert 555 in bot.userbot._sessions
    assert bot.userbot.has_pending_takeover_request(555) is False
    assert update.callback_query.answers == [{"text": None, "show_alert": False}]
    assert "Takeover started on 555" in update.callback_query.edits[-1]


def test_no_button_dismisses_request_in_both_channels():
    bot = _bot()
    bot.userbot.observe_incoming(556, "hello", 1, 1.0, "Sender", "sender")
    update = FakeCallbackUpdate("hive_takeover:no:556")

    _run(bot._callback_takeover_request(update, FakeContext([])))

    assert 556 not in bot.userbot._sessions
    assert bot.userbot.has_pending_takeover_request(556) is False
    assert "dismissed" in update.callback_query.edits[-1]


def test_takeover_buttons_reject_unauthorised_users():
    bot = _bot()
    bot.userbot.observe_incoming(557, "hello", 1, 1.0)
    update = FakeCallbackUpdate("hive_takeover:yes:557", uid=999)

    _run(bot._callback_takeover_request(update, FakeContext([])))

    assert 557 not in bot.userbot._sessions
    assert bot.userbot.has_pending_takeover_request(557) is True
    assert update.callback_query.answers == [
        {"text": "Unauthorised.", "show_alert": True}
    ]
    assert update.callback_query.edits == []


def test_takeovers_lists_active_sessions_with_stop_buttons():
    bot = _bot()
    bot.userbot.begin_takeover(601, "confused_elderly")
    update = FakeUpdate()

    _run(bot._cmd_takeovers(update, FakeContext([])))

    assert "Active takeovers" in update.message.replies[-1]
    assert "601" in update.message.replies[-1]
    button = update.message.reply_markups[-1].inline_keyboard[0][0]
    assert button.text == "Stop & seal 601"
    assert button.callback_data == "hive_seal:request:601"


def test_status_without_args_shows_active_takeover_buttons():
    bot = _bot()
    bot.userbot.begin_takeover(606, "confused_elderly")
    update = FakeUpdate()

    _run(bot._cmd_status(update, FakeContext([])))

    assert update.message.replies[-1] == "Choose a takeover to inspect:"
    button = update.message.reply_markups[-1].inline_keyboard[0][0]
    assert button.callback_data == "hive_status:show:606"


def test_status_button_opens_summary_and_stop_control():
    bot = _bot()
    bot.userbot.begin_takeover(607, "confused_elderly")
    update = FakeCallbackUpdate("hive_status:show:607")

    _run(bot._callback_status(update, FakeContext([])))

    assert update.callback_query.edits[-1] == "summary"
    button = update.callback_query.edit_markups[-1].inline_keyboard[0][0]
    assert button.callback_data == "hive_seal:request:607"


def test_interactive_status_and_persona_reject_unauthorised_users():
    bot = _bot()
    bot.userbot.begin_takeover(608, "confused_elderly")
    status_update = FakeCallbackUpdate("hive_status:show:608", uid=999)
    persona_update = FakeCallbackUpdate("hive_persona:set:confused_elderly", uid=999)

    _run(bot._callback_status(status_update, FakeContext([])))
    _run(bot._callback_persona(persona_update, FakeContext([])))

    expected = [{"text": "Unauthorised.", "show_alert": True}]
    assert status_update.callback_query.answers == expected
    assert persona_update.callback_query.answers == expected
    assert status_update.callback_query.edits == []
    assert persona_update.callback_query.edits == []


def test_stop_command_requires_confirmation_and_keeps_takeover_running():
    bot = _bot()
    bot.userbot.begin_takeover(602, "confused_elderly")
    update = FakeUpdate()

    _run(bot._cmd_stop(update, FakeContext(["602"])))

    assert 602 in bot.userbot._sessions
    assert "Stop and seal takeover 602?" in update.message.replies[-1]
    buttons = update.message.reply_markups[-1].inline_keyboard[0]
    assert [button.callback_data for button in buttons] == [
        "hive_seal:confirm:602",
        "hive_seal:cancel:602",
    ]


def test_confirm_button_seals_archives_and_returns_pdf(tmp_path):
    store = TakeoverHistoryStore(tmp_path / "history")
    bot = _bot(store)
    bot.takeovers.evidence_root = tmp_path / "evidence"
    bot.userbot.begin_takeover(603, "confused_elderly")
    bot.userbot._sessions[603][0].messages.append(Message("stranger", "hello", 1.0, 1))
    update = FakeCallbackUpdate("hive_seal:confirm:603")

    _run(bot._callback_seal(update, FakeContext([])))

    assert 603 not in bot.userbot._sessions
    assert store.list()[0]["peer_id"] == 603
    assert "stopped and sealed" in update.callback_query.edits[-1]
    assert update.callback_query.message.documents == [("evidence_603.pdf", b"pdf")]


def test_seal_failure_keeps_takeover_active(tmp_path):
    bot = _bot(TakeoverHistoryStore(tmp_path / "history"))
    bot.engine.fail_seal = True
    bot.userbot.begin_takeover(604, "confused_elderly")
    update = FakeCallbackUpdate("hive_seal:confirm:604")

    _run(bot._callback_seal(update, FakeContext([])))

    assert 604 in bot.userbot._sessions
    assert "remains active" in update.callback_query.edits[-1]
    assert update.callback_query.message.documents == []


def test_seal_buttons_reject_unauthorised_users():
    bot = _bot()
    bot.userbot.begin_takeover(605, "confused_elderly")
    update = FakeCallbackUpdate("hive_seal:confirm:605", uid=999)

    _run(bot._callback_seal(update, FakeContext([])))

    assert 605 in bot.userbot._sessions
    assert update.callback_query.answers == [
        {"text": "Unauthorised.", "show_alert": True}
    ]
