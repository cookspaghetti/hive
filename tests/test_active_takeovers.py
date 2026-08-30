"""Restart-safety tests for unfinished takeover checkpoints."""

import asyncio
import threading

import pytest

from hive.active_takeovers import (
    ACTIVE,
    PAUSED_AFTER_RESTART,
    FileActiveTakeoverStore,
)
from hive.runtime import TurnOutput
from hive.state import HVI, Message, Phase, SessionState
from hive.transports.userbot import UserbotTransport
from hive.vault.hashchain import HashChain


def _run(coro):
    try:
        original = asyncio.get_running_loop()
    except RuntimeError:
        original = asyncio.new_event_loop()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(original)


class RecoveryEngine:
    def __init__(self):
        self.processed: list[int] = []

    def new_session(self, peer_id, persona):
        return SessionState(peer_id=peer_id, persona=persona), HashChain()

    def process_messages(self, session, chain, messages, *, record_outbound=True):
        self.processed.extend(message.msg_id for message in messages)
        for message in messages:
            session.messages.append(message)
            chain.append(
                {"event": "msg_in", "msg_id": message.msg_id, "text": message.text},
                ts=message.ts,
            )
        session.turn_count += len(messages)
        session.exchange_count += 1
        session.phase = Phase.ACTIVE
        return TurnOutput(text="recovery reply", delay_s=0.0)

    def record_outbound(self, session, chain, text):
        message = Message("agent", text, 50.0, session.next_agent_msg_id)
        session.next_agent_msg_id -= 1
        session.messages.append(message)
        chain.append(
            {"event": "msg_out", "msg_id": message.msg_id, "text": text},
            ts=message.ts,
        )
        return message

    def forget(self, _peer_id):
        return None


def test_file_checkpoint_round_trips_session_chain_and_pending_messages(tmp_path):
    store = FileActiveTakeoverStore(tmp_path)
    session = SessionState(peer_id=77, persona="confused_elderly", phase=Phase.ACTIVE)
    session.verdict = "likely_scam"
    session.hvis.append(HVI("bank_account", "12345678", 4, 0.9, "regex"))
    session.threat_intelligence.append(
        {
            "provider": "semak_mule",
            "observable": "12345678",
            "status": "hit",
            "risk": "malicious",
        }
    )
    session.indicator_reviews.append(
        {
            "id": "review-1",
            "original": {"kind": "phone", "value": "12345678"},
            "corrected": {"kind": "bank_account", "value": "12345678"},
            "reason": "operator verified the source",
        }
    )
    session.messages.append(Message("stranger", "pay now", 10.0, 4))
    chain = HashChain()
    chain.append({"event": "msg_in", "msg_id": 4, "text": "pay now"}, ts=10.0)
    pending = [Message("stranger", "hello?", 11.0, 5)]

    store.save(
        session,
        chain,
        pending_messages=pending,
        recovery_status=ACTIVE,
    )
    restored = store.list()[0]

    assert restored.session.session_id == session.session_id
    assert restored.session.phase is Phase.ACTIVE
    assert restored.session.hvis[0].value == "12345678"
    assert restored.session.threat_intelligence[0]["provider"] == "semak_mule"
    assert restored.session.indicator_reviews[0]["id"] == "review-1"
    assert restored.pending_messages[0].msg_id == 5
    assert restored.chain.verify() is True
    store.delete(session.session_id)
    assert store.list() == []


def test_checkpoint_rejects_a_tampered_hash_chain(tmp_path):
    store = FileActiveTakeoverStore(tmp_path)
    session = SessionState(peer_id=78, persona="confused_elderly")
    chain = HashChain()
    chain.append({"event": "msg_in", "text": "original"}, ts=1.0)
    chain.entries[0].payload["text"] = "tampered"

    with pytest.raises(ValueError, match="invalid evidence hash chain"):
        store.save(session, chain)


def test_restored_takeover_stays_paused_until_operator_resumes(tmp_path):
    store = FileActiveTakeoverStore(tmp_path)
    original = SessionState(
        peer_id=79,
        persona="naive_young_adult",
        phase=Phase.ACTIVE,
    )
    original.started_ts = 1.0
    store.save(
        original,
        HashChain(),
        pending_messages=[Message("stranger", "first", 10.0, 10)],
    )
    engine = RecoveryEngine()
    transport = UserbotTransport(
        1,
        "hash",
        "session",
        engine,
        inbox_debounce_s=0,
        inbox_max_wait_s=0,
        checkpoint_store=store,
    )
    sent: list[str] = []

    async def send_as_user(_peer_id, text):
        sent.append(text)

    transport.send_as_user = send_as_user

    assert transport.recovery_status(79) == PAUSED_AFTER_RESTART
    assert engine.processed == []
    _run(transport.on_message(79, "second", 11, 11.0))
    assert engine.processed == []
    assert sent == []
    assert [message.msg_id for message in store.list()[0].pending_messages] == [10, 11]

    assert _run(transport.resume_recovery(79)) == 2
    assert engine.processed == [10, 11]
    assert sent == ["recovery reply"]
    assert transport.recovery_status(79) == ACTIVE
    checkpoint = store.list()[0]
    assert checkpoint.pending_messages == []
    assert [message.role for message in checkpoint.session.messages] == [
        "stranger",
        "stranger",
        "agent",
    ]

    restarted = UserbotTransport(
        1,
        "hash",
        "session",
        RecoveryEngine(),
        checkpoint_store=store,
    )
    assert restarted.recovery_status(79) == PAUSED_AFTER_RESTART
    assert restarted._takeover_seed_messages.get(79, []) == []
    restarted.abandon_recovery(79)
    assert store.list() == []


def test_terminal_checkpoint_is_removed_instead_of_restored(tmp_path):
    store = FileActiveTakeoverStore(tmp_path)
    session = SessionState(
        peer_id=80,
        persona="confused_elderly",
        phase=Phase.SEALED,
    )
    store.save(session, HashChain())

    transport = UserbotTransport(
        1,
        "hash",
        "session",
        RecoveryEngine(),
        checkpoint_store=store,
    )

    assert transport._sessions == {}
    assert store.list() == []


def test_premature_benign_closing_checkpoint_is_restored_as_active(tmp_path):
    store = FileActiveTakeoverStore(tmp_path)
    session = SessionState(
        peer_id=81,
        persona="confused_elderly",
        phase=Phase.CLOSING,
        verdict="likely_benign",
        exchange_count=3,
        turn_count=6,
    )
    store.save(session, HashChain())
    engine = RecoveryEngine()
    engine.early_exit_min_turns = 10

    transport = UserbotTransport(
        1,
        "hash",
        "session",
        engine,
        checkpoint_store=store,
    )

    assert transport._sessions[81][0].phase is Phase.ACTIVE
    assert transport.recovery_status(81) == PAUSED_AFTER_RESTART
    assert store.list()[0].session.phase is Phase.ACTIVE


def test_checkpoint_keeps_inflight_and_newly_buffered_messages(tmp_path):
    class BlockingEngine(RecoveryEngine):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def process_messages(self, session, chain, messages, *, record_outbound=True):
            if messages[0].msg_id == 1:
                self.started.set()
                assert self.release.wait(timeout=5)
            self.processed.extend(message.msg_id for message in messages)
            session.messages.extend(messages)
            for message in messages:
                chain.append(
                    {"event": "msg_in", "msg_id": message.msg_id, "text": message.text},
                    ts=message.ts,
                )
            return TurnOutput(text=None)

    store = FileActiveTakeoverStore(tmp_path)
    engine = BlockingEngine()
    transport = UserbotTransport(
        1,
        "hash",
        "session",
        engine,
        inbox_debounce_s=0,
        inbox_max_wait_s=0,
        checkpoint_store=store,
    )
    transport.begin_takeover(81, "confused_elderly")

    async def scenario():
        first = asyncio.create_task(transport.on_message(81, "first", 1, 1.0))
        while not engine.started.is_set():
            await asyncio.sleep(0.005)
        second = asyncio.create_task(transport.on_message(81, "second", 2, 2.0))
        await asyncio.sleep(0.02)
        pending = [message.msg_id for message in store.list()[0].pending_messages]
        engine.release.set()
        await asyncio.gather(first, second)
        return pending

    assert _run(scenario()) == [1, 2]
    assert engine.processed == [1, 2]
    assert store.list()[0].pending_messages == []


def test_failed_checkpoint_delete_keeps_takeover_in_memory(tmp_path):
    class DeleteFailureStore(FileActiveTakeoverStore):
        def delete(self, session_id):
            raise RuntimeError(f"database unavailable for {session_id}")

    store = DeleteFailureStore(tmp_path)
    transport = UserbotTransport(
        1,
        "hash",
        "session",
        RecoveryEngine(),
        checkpoint_store=store,
    )
    transport.begin_takeover(82, "confused_elderly")

    with pytest.raises(RuntimeError, match="database unavailable"):
        transport.end_takeover(82)

    assert 82 in transport._sessions
    assert len(store.list()) == 1
