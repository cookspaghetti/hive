"""Completed takeover history persistence tests."""

from hive.history import (
    PostgresTakeoverHistoryStore,
    TakeoverHistoryStore,
    build_history_store,
)
from hive.state import HVI, Message, SessionState


def test_history_store_persists_and_lists_newest_first(tmp_path):
    store = TakeoverHistoryStore(tmp_path)
    older = SessionState(peer_id=10, persona="confused_elderly", started_ts=10)
    older.messages.append(Message("stranger", "first chat", 11, 1))
    store.archive(older, ended_ts=20)

    newer = SessionState(peer_id=20, persona="small_business_owner", started_ts=30)
    newer.messages.append(Message("agent", "second chat", 31, 2))
    newer.hvis.append(HVI("bank_account", "123", 2, 0.9))
    archived = store.archive(newer, evidence_path="bundle_20.pdf", ended_ts=40)

    rows = store.list()
    assert [row["peer_id"] for row in rows] == [20, 10]
    assert rows[0]["message_count"] == 1
    assert store.get(archived["id"]) == archived


def test_history_store_ignores_invalid_and_corrupt_records(tmp_path):
    store = TakeoverHistoryStore(tmp_path)
    tmp_path.joinpath("broken.json").write_text("not-json", encoding="utf-8")

    assert store.list() == []
    assert store.get("../broken") is None


def test_history_store_factory_selects_postgres_only_when_configured(tmp_path):
    local = build_history_store(tmp_path)
    postgres = build_history_store(tmp_path, "postgresql://hive:test@postgres/hive")

    assert isinstance(local, TakeoverHistoryStore)
    assert isinstance(postgres, PostgresTakeoverHistoryStore)
