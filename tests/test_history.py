"""Completed takeover history persistence tests."""

import json
from uuid import UUID

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
    newer.exchange_count = 1
    newer.messages.append(Message("agent", "second chat", 31, 2))
    newer.hvis.append(HVI("bank_account", "123", 2, 0.9))
    archived = store.archive(newer, evidence_path="bundle_20.pdf", ended_ts=40)

    rows = store.list()
    assert [row["peer_id"] for row in rows] == [20, 10]
    assert rows[0]["message_count"] == 1
    assert rows[0]["session_id"] == newer.session_id
    assert store.get(archived["id"])["exchanges"] == 1
    assert store.get(archived["id"]) == archived
    assert UUID(archived["id"]).version == 4
    assert archived["hvi_items"][0]["source_msg_id"] == 2
    assert UUID(archived["analysis"]["id"]).version == 4
    assert archived["analysis"]["history_id"] == archived["id"]
    assert archived["analysis"]["kind"] == "original"
    assert archived["analysis"]["schema_version"] == 1
    assert len(archived["analysis"]["transcript_sha256"]) == 64
    assert rows[0]["analysis_run_id"] == archived["analysis"]["id"]


def test_history_store_migrates_legacy_ids_and_keeps_old_alias(tmp_path):
    legacy_id = "123456789_20"
    record = {"id": legacy_id, "peer_id": 20, "ended_ts": 40, "messages": []}
    tmp_path.joinpath(f"{legacy_id}.json").write_text(json.dumps(record), encoding="utf-8")
    store = TakeoverHistoryStore(tmp_path)

    migrated = store.migrate_legacy_ids()

    new_id = migrated[legacy_id]
    assert UUID(new_id).version == 4
    assert store.get(new_id)["legacy_id"] == legacy_id
    assert store.get(legacy_id)["id"] == new_id
    assert not tmp_path.joinpath(f"{legacy_id}.json").exists()


def test_history_store_backfills_analysis_without_changing_transcript(tmp_path):
    history_id = "8514213f-a1eb-4986-a2dc-bd3fa196ea96"
    messages = [{"role": "stranger", "text": "你好", "ts": 10, "msg_id": 1}]
    record = {
        "id": history_id,
        "peer_id": 20,
        "ended_ts": 40,
        "messages": messages,
    }
    tmp_path.joinpath(f"{history_id}.json").write_text(
        json.dumps(record, ensure_ascii=False),
        encoding="utf-8",
    )
    store = TakeoverHistoryStore(tmp_path)

    migrated = store.migrate_analysis_metadata()

    updated = store.get(history_id)
    assert migrated[history_id] == updated["analysis"]["id"]
    assert updated["analysis"]["kind"] == "original"
    assert updated["messages"] == messages
    assert store.migrate_analysis_metadata() == {}


def test_history_store_imports_prepared_uuid_record(tmp_path):
    store = TakeoverHistoryStore(tmp_path)
    record = {
        "id": "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
        "peer_id": 919,
        "ended_ts": 40,
        "messages": [],
    }

    store.import_record(record)

    assert store.get(record["id"]) == record


def test_history_list_hides_reanalysis_replay_but_keeps_direct_lookup(tmp_path):
    store = TakeoverHistoryStore(tmp_path)
    original = {
        "id": "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
        "peer_id": 919,
        "ended_ts": 40,
        "messages": [{"role": "stranger", "text": "original"}],
        "analysis": {"kind": "original"},
    }
    replay = {
        "id": "8d195b38-c1c3-4e00-ac1a-bcc5851ea2be",
        "peer_id": 919,
        "ended_ts": 40,
        "messages": [{"role": "stranger", "text": "replay"}],
        "replay_of": "legacy-id",
        "analysis": {"kind": "legacy_reanalysis"},
    }
    store.import_record(original)
    store.import_record(replay)

    assert [row["id"] for row in store.list()] == [original["id"]]
    assert store.get(replay["id"]) == replay


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
