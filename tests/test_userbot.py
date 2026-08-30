"""Tests for UserbotTransport hand-back behaviour (review fix P1).

Verifies that a benign early-exit ends the takeover (peer removed from
_sessions) and fires the on_handback callback. Uses a fake engine so no
Telegram/LLM is needed; on_message never touches the Telethon client on the
hand-back path.
"""

import asyncio
import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from hive.active_takeovers import PAUSED_AFTER_LIMIT, FileActiveTakeoverStore
from hive.runtime import TurnOutput
from hive.state import Message, Phase
from hive.transports.userbot import UserbotTransport


class FakeEngine:
    """Duck-typed engine: returns handed_back or a normal reply on demand."""

    def __init__(self, handed_back: bool, *, terminated: bool = False):
        self._handed_back = handed_back
        self._terminated = terminated
        self.closed = False

    def new_session(self, peer_id, persona):
        # minimal session object with the attributes the transport/callbacks use
        from hive.state import SessionState
        from hive.vault.hashchain import HashChain
        return SessionState(peer_id=peer_id, persona=persona), HashChain()

    def process_turn(self, session, chain, inbound):
        if self._handed_back or self._terminated:
            session.phase = Phase.CLOSING
        return TurnOutput(
            text=None if self._handed_back or self._terminated else "hi",
            handed_back=self._handed_back,
            terminated=self._terminated,
            reason="max_turns" if self._terminated else "",
            delay_s=2.0,
        )


def _transport(engine, **kwargs):
    return UserbotTransport(
        1,
        "hash",
        "sess",
        engine,
        inbox_debounce_s=0.0,
        inbox_max_wait_s=0.0,
        **kwargs,
    )


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def test_benign_handback_ends_takeover_and_notifies():
    fired = {}

    async def on_handback(peer_id, session):
        fired["peer"] = peer_id

    ub = _transport(FakeEngine(handed_back=True), on_handback=on_handback)
    ub.begin_takeover(555, "confused_elderly")
    assert 555 in ub._sessions

    _run(ub.on_message(555, "ok bye thanks", 1, 0.0))

    assert 555 not in ub._sessions          # takeover ended
    assert fired.get("peer") == 555         # operator notified


def test_handback_notification_failure_does_not_restore_takeover():
    async def broken_notification(peer_id, session):
        raise RuntimeError("control bot unavailable")

    ub = _transport(FakeEngine(handed_back=True), on_handback=broken_notification)
    ub.begin_takeover(556, "confused_elderly")

    _run(ub.on_message(556, "ok bye thanks", 1, 0.0))

    assert 556 not in ub._sessions


def test_budget_limit_pauses_and_preserves_takeover_for_operator(tmp_path):
    notifications = []
    handbacks = []

    async def on_limit(peer_id, session, reason):
        notifications.append((peer_id, session.session_id, reason))

    async def on_handback(peer_id, session):
        handbacks.append((peer_id, session.session_id))

    store = FileActiveTakeoverStore(tmp_path / "active")
    ub = _transport(
        FakeEngine(handed_back=False, terminated=True),
        on_limit_reached=on_limit,
        on_handback=on_handback,
        checkpoint_store=store,
    )
    ub.begin_takeover(557, "confused_elderly")

    _run(ub.on_message(557, "still there?", 1, 1.0))

    assert 557 in ub._sessions
    assert ub.recovery_status(557) == PAUSED_AFTER_LIMIT
    assert notifications == [(557, ub._sessions[557][0].session_id, "max_turns")]
    assert handbacks == []
    checkpoint = store.list()[0]
    assert checkpoint.recovery_status == PAUSED_AFTER_LIMIT

    _run(ub.on_message(557, "hello again", 2, 2.0))
    assert [message.msg_id for message in checkpoint.pending_messages] == []
    refreshed = store.list()[0]
    assert [message.msg_id for message in refreshed.pending_messages] == [2]


def test_active_conversation_keeps_session():
    ub = _transport(FakeEngine(handed_back=False))
    ub.begin_takeover(777, "naive_young_adult")
    # send_as_user would need a live client; monkeypatch it to a no-op
    async def _noop(peer_id, text):
        pass
    ub.send_as_user = _noop
    _run(ub.on_message(777, "tell me more", 1, 0.0))
    assert 777 in ub._sessions              # still under takeover


def test_on_message_ignores_unknown_peer():
    ub = _transport(FakeEngine(handed_back=True))
    # no takeover started for this peer -> no error, no state
    _run(ub.on_message(999, "hello", 1, 0.0))
    assert 999 not in ub._sessions


def test_processing_status_tracks_queued_and_inflight_messages():
    ub = _transport(FakeEngine(handed_back=False))
    assert ub.is_processing(321) is False

    message = Message("stranger", "hello", 1.0, 1)
    ub._inbound_buffers[321] = [message]
    assert ub.is_processing(321) is True

    ub._inbound_buffers.clear()
    ub._inflight_batches[321] = [message]
    assert ub.is_processing(321) is True

    ub._inflight_batches.clear()
    assert ub.is_processing(321) is False


def test_media_analysis_overlaps_debounce_and_reaches_batch_processor(tmp_path):
    from hive.state import HVI

    class MediaEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.seen = []

        def analyze_media(self, message):
            return {
                "analysis": {
                    "source_msg_id": message.msg_id,
                    "source": "local_ocr",
                    "description": "account 12345678",
                },
                "hvis": [HVI("bank_account", "12345678", message.msg_id, 0.8, "ocr")],
            }

        def process_messages(self, session, chain, messages):
            self.seen.extend(messages)
            return TurnOutput(text=None)

    engine = MediaEngine()
    ub = _transport(engine)
    ub.begin_takeover(778, "naive_young_adult")
    image = tmp_path / "receipt.jpg"
    image.write_bytes(b"jpeg")

    _run(
        ub.on_message(
            778,
            "receipt",
            4,
            1.0,
            media_kind="image",
            media_path=str(image),
            media_sha256="abc",
        )
    )

    assert engine.seen[0].media_analysis["source"] == "local_ocr"
    assert engine.seen[0].media_hvis[0].value == "12345678"


def test_media_analysis_failure_does_not_block_batch_processing(tmp_path):
    class FailingMediaEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.processed = False

        def analyze_media(self, message):
            raise RuntimeError("bad image")

        def process_messages(self, session, chain, messages):
            self.processed = True
            return TurnOutput(text=None)

    engine = FailingMediaEngine()
    ub = _transport(engine)
    ub.begin_takeover(779, "naive_young_adult")
    image = tmp_path / "broken.jpg"
    image.write_bytes(b"broken")

    _run(ub.on_message(779, "image", 5, 1.0, media_kind="image", media_path=str(image)))

    assert engine.processed is True


def test_media_metadata_preserves_image_and_original_document_names():
    image = SimpleNamespace(
        id=12,
        message=SimpleNamespace(
            file=SimpleNamespace(name=None, mime_type="image/jpeg", ext=".jpg", size=123),
            photo=object(),
            video=False,
            video_note=False,
            voice=False,
            audio=False,
            sticker=None,
        ),
    )
    document = SimpleNamespace(
        id=13,
        message=SimpleNamespace(
            file=SimpleNamespace(
                name="../../bank statement.pdf",
                mime_type="application/pdf",
                ext=".pdf",
                size=456,
            ),
            photo=None,
            video=False,
            video_note=False,
            voice=False,
            audio=False,
            sticker=None,
        ),
    )

    assert UserbotTransport._describe_media(image) == {
        "media_kind": "image",
        "media_name": "telegram_12.jpg",
        "media_mime": "image/jpeg",
        "media_size": 123,
    }
    assert UserbotTransport._describe_media(document)["media_name"] == "bank statement.pdf"


def test_media_capture_is_bounded_and_stored_under_the_session(tmp_path):
    ub = _transport(FakeEngine(handed_back=True), media_root=tmp_path, media_max_bytes=100)
    ub.begin_takeover(321, "confused_elderly")
    payload = b"image bytes"

    class FakeEvent:
        id = 77

        async def download_media(self, file):
            path = Path(file)
            path.write_bytes(payload)
            return str(path)

    media = {
        "media_kind": "image",
        "media_name": "receipt.jpg",
        "media_mime": "image/jpeg",
        "media_size": len(payload),
    }
    captured = _run(ub._capture_media(FakeEvent(), 321, media))

    assert captured["media_size"] == len(payload)
    assert captured["media_sha256"] == hashlib.sha256(payload).hexdigest()
    captured_path = Path(captured["media_path"])
    assert captured_path.parent.name == ub._sessions[321][0].session_id
    assert captured_path.name == "77_receipt.jpg"

    oversized = {**media, "media_size": 101}
    assert _run(ub._capture_media(FakeEvent(), 321, oversized)) == {}


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
            "display_name": "Alice",
            "latest_text": "latest",
            "latest_ts": 20.0,
            "request_pending": True,
            "request_created_at": 10.0,
        }
    ]

    ub.begin_takeover(999, "confused_elderly")
    assert ub.list_observed_chats()[0]["active"] is True
    assert ub.list_observed_chats()[0]["request_pending"] is False


def test_takeover_preserves_trigger_messages_and_observed_identity():
    class SeedEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.seen = []

        def process_messages(self, session, chain, messages):
            self.seen.extend(messages)
            session.messages.extend(messages)
            for message in messages:
                chain.append(
                    {"event": "msg_in", "msg_id": message.msg_id, "text": message.text},
                    ts=message.ts,
                )
            session.turn_count += len(messages)
            session.exchange_count += 1
            return TurnOutput(text=None)

    engine = SeedEngine()
    ub = _transport(engine)
    ub.observe_incoming(
        1001,
        "first suspicious message",
        41,
        100.0,
        "Alice Tan",
        "alice_t",
        captured_ts=101.0,
    )
    ub.observe_incoming(
        1001,
        "send money now",
        42,
        102.0,
        captured_ts=103.0,
    )

    ub.begin_takeover(1001, "confused_elderly")
    session = ub._sessions[1001][0]
    assert session.peer_display_name == "Alice Tan"
    assert session.peer_username == "alice_t"
    assert session.identity_observed_ts == 103.0

    processed = _run(ub.process_pending_takeover(1001))

    assert processed == 2
    assert [message.msg_id for message in engine.seen] == [41, 42]
    assert all(message.pre_takeover for message in engine.seen)
    assert [message.captured_ts for message in engine.seen] == [101.0, 103.0]


def test_unprocessed_trigger_is_preserved_when_takeover_is_sealed_immediately():
    ub = _transport(FakeEngine(handed_back=False))
    ub.observe_incoming(1002, "urgent transfer", 51, 200.0, captured_ts=201.0)
    ub.begin_takeover(1002, "confused_elderly")

    session, chain = ub.end_takeover(1002)

    assert session.messages == [
        Message(
            "stranger",
            "urgent transfer",
            200.0,
            51,
            captured_ts=201.0,
            pre_takeover=True,
        )
    ]
    assert chain.verify() is True


def test_takeover_request_notifies_once_and_reopens_after_takeover():
    delivered = []

    async def notify(chat):
        delivered.append(chat)
        return True

    ub = _transport(FakeEngine(handed_back=False), on_takeover_request=notify)
    ub.observe_incoming(444, "first", 1, 10.0, "Sender", "sender")
    ub.observe_incoming(444, "latest", 2, 11.0)

    _run(ub.notify_pending_takeover_requests())
    _run(ub.notify_pending_takeover_requests())

    assert len(delivered) == 1
    assert delivered[0]["latest_text"] == "latest"
    assert delivered[0]["message_count"] == 2
    ub.begin_takeover(444, "confused_elderly")
    ub.end_takeover(444)
    ub.observe_incoming(444, "new request", 3, 20.0)
    _run(ub.notify_pending_takeover_requests())
    assert len(delivered) == 2
    assert delivered[-1]["request_created_at"] == 20.0


def test_pending_takeover_notification_refreshes_after_new_message():
    delivered = []

    async def notify(chat):
        delivered.append(chat)
        return True

    ub = _transport(FakeEngine(handed_back=False), on_takeover_request=notify)
    ub.observe_incoming(447, "first", 1, 10.0)
    _run(ub.notify_pending_takeover_requests())
    ub.observe_incoming(447, "updated", 2, 11.0)
    _run(ub.notify_pending_takeover_requests())

    assert [item["latest_text"] for item in delivered] == ["first", "updated"]
    assert delivered[-1]["message_count"] == 2


def test_userbot_mutations_advance_panel_sync_revision():
    from hive.webpanel.observability import get_observation_hub

    hub = get_observation_hub()
    before = hub.change_state()["revision"]
    ub = _transport(FakeEngine(handed_back=False))

    ub.observe_incoming(448, "new request", 1, 10.0)
    after_request = hub.change_state()["revision"]
    ub.begin_takeover(448, "confused_elderly")
    after_takeover = hub.change_state()["revision"]
    ub.update_persona(448, "small_business_owner")
    after_persona = hub.change_state()["revision"]

    assert before < after_request < after_takeover < after_persona


def test_failed_takeover_request_delivery_is_retried():
    attempts = 0

    async def notify(chat):
        nonlocal attempts
        attempts += 1
        return attempts > 1

    ub = _transport(FakeEngine(handed_back=False), on_takeover_request=notify)
    ub.observe_incoming(445, "hello", 1, 10.0)

    _run(ub.notify_pending_takeover_requests())
    _run(ub.notify_pending_takeover_requests())

    assert attempts == 2


def test_dismissed_request_leaves_queue_and_can_reopen_on_new_message():
    ub = _transport(FakeEngine(handed_back=False))
    ub.observe_incoming(446, "first", 1, 10.0)

    assert ub.dismiss_takeover_request(446) is True
    assert ub.dismiss_takeover_request(446) is False
    assert ub.has_pending_takeover_request(446) is False
    ub.observe_incoming(446, "new message", 2, 20.0)
    assert ub.has_pending_takeover_request(446) is True


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


def test_message_burst_is_processed_once_and_sent_as_separate_bubbles():
    class BatchEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.batches = []

        def process_messages(self, session, chain, messages):
            self.batches.append(messages)
            return TurnOutput(
                text="wait ah\n\nwhich account?",
                delay_s=0.0,
                messages=("wait ah", "which account?"),
                message_delays_s=(0.0, 0.0),
            )

    engine = BatchEngine()
    ub = UserbotTransport(
        1,
        "hash",
        "sess",
        engine,
        inbox_debounce_s=0.02,
        inbox_max_wait_s=0.08,
    )
    sent = []

    async def send(peer_id, text):
        sent.append((peer_id, text))

    ub.send_as_user = send
    ub.begin_takeover(808, "confused_elderly")

    async def scenario():
        first = asyncio.create_task(ub.on_message(808, "hello", 1, 1.0))
        await asyncio.sleep(0.005)
        second = asyncio.create_task(ub.on_message(808, "are you there?", 2, 2.0))
        await asyncio.gather(first, second)

    _run(scenario())

    assert [[message.text for message in batch] for batch in engine.batches] == [
        ["hello", "are you there?"]
    ]
    assert sent == [(808, "wait ah"), (808, "which account?")]


def test_new_message_during_thinking_can_discard_stale_draft():
    class SteeringEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.batches = []
            self.recorded = []

        def process_messages(self, session, chain, messages, *, record_outbound=True):
            assert record_outbound is False
            self.batches.append([message.text for message in messages])
            time.sleep(0.04)
            latest = messages[-1].text
            return TurnOutput(
                text=f"reply to {latest}",
                messages=(f"reply to {latest}",),
                message_delays_s=(0.0,),
            )

        def steer_pending_reply(self, session, pending_text, new_inbounds):
            return "continue"

        def record_outbound(self, session, chain, text):
            self.recorded.append(text)

    engine = SteeringEngine()
    ub = _transport(engine)
    sent = []

    async def send(peer_id, text):
        sent.append(text)

    ub.send_as_user = send
    ub.begin_takeover(909, "confused_elderly")

    async def scenario():
        first = asyncio.create_task(ub.on_message(909, "first", 1, 1.0))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(ub.on_message(909, "changed detail", 2, 2.0))
        await asyncio.gather(first, second)

    _run(scenario())

    assert engine.batches == [["first"], ["changed detail"]]
    assert sent == ["reply to changed detail"]
    assert engine.recorded == sent


def test_new_message_during_thinking_can_send_one_draft_before_reconsidering():
    class SteeringEngine(FakeEngine):
        def __init__(self):
            super().__init__(handed_back=False)
            self.steer_calls = 0

        def process_messages(self, session, chain, messages, *, record_outbound=True):
            time.sleep(0.04)
            latest = messages[-1].text
            return TurnOutput(
                text=f"reply to {latest}",
                messages=(f"reply to {latest}", "unused second bubble"),
                message_delays_s=(0.0, 0.0),
                message_typing_s=(0.0, 0.0),
                pace="fast",
            )

        def steer_pending_reply(self, session, pending_text, new_inbounds):
            self.steer_calls += 1
            return "send_first"

        def record_outbound(self, session, chain, text):
            return None

    engine = SteeringEngine()
    ub = _transport(engine)
    sent = []

    async def send(peer_id, text):
        sent.append(text)

    ub.send_as_user = send
    ub.begin_takeover(910, "naive_young_adult")

    async def scenario():
        first = asyncio.create_task(ub.on_message(910, "first", 1, 1.0))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(ub.on_message(910, "one more thing", 2, 2.0))
        await asyncio.gather(first, second)

    _run(scenario())

    assert sent == ["reply to first", "reply to one more thing", "unused second bubble"]
    assert engine.steer_calls == 1
