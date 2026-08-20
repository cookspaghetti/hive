"""Archived takeover analysis replay tests."""

from hive.reanalyze import _archived_records
from hive.replay import replay_history_record
from hive.runtime import HiveEngine
from tests.fakes import fake_client


class FakeSandbox:
    pass


class FakeNer:
    def predict(self, text, labels):
        if text == "I am John":
            return [("person name", "John", 0.8)]
        if "phone number" in text:
            return [("phone number", "phone number", 0.9)]
        return []


def test_replay_preserves_transcript_and_reruns_grounded_analysis():
    record = {
        "id": "123_919",
        "peer_id": 919,
        "persona": "confused_elderly",
        "started_ts": 10,
        "signal_trail": [{"turn": 1}, {"turn": 2}],
        "messages": [
            {"role": "stranger", "text": "I am John", "ts": 11, "msg_id": 1},
            {"role": "agent", "text": "hello John", "ts": 12, "msg_id": -1},
            {
                "role": "stranger",
                "text": "Just send me your phone number",
                "ts": 13,
                "msg_id": 2,
            },
        ],
    }
    classifier = (
        '{"urgency":{"score":0.0,"message_ids":[]},'
        '"payment_request":{"score":0.7,"message_ids":[2]}}'
    )
    engine = HiveEngine(
        agent_client=fake_client(classifier),
        sandbox_runner=FakeSandbox(),
        ner_backend=FakeNer(),
        enable_early_exit=False,
    )

    replayed = replay_history_record(record, engine)

    assert [(message.role, message.text) for message in replayed.messages] == [
        ("stranger", "I am John"),
        ("agent", "hello John"),
        ("stranger", "Just send me your phone number"),
    ]
    assert [(item.kind, item.value) for item in replayed.hvis] == [("person_name", "John")]
    assert replayed.replay_of == "123_919"
    assert replayed.turn_count == 2
    contributions = replayed.signal_trail[-1]["contributions"]
    assert all(item["reason"] != "soft:payment_request" for item in contributions)
    assert contributions[-1]["source_message_ids"] == [1]


def test_replay_recovers_cross_message_agent_alias():
    record = {
        "id": "alias_919",
        "peer_id": 919,
        "persona": "naive_young_adult",
        "started_ts": 10,
        "signal_trail": [{"turn": 3}],
        "messages": [
            {
                "role": "agent",
                "text": "the bank account under what name ah?",
                "ts": 11,
                "msg_id": -1,
            },
            {"role": "stranger", "text": "yes quick", "ts": 12, "msg_id": 20},
            {"role": "stranger", "text": "petasan", "ts": 13, "msg_id": 21},
            {"role": "stranger", "text": "this my agent", "ts": 14, "msg_id": 22},
        ],
    }
    classifier = (
        '{"urgency":{"score":0.0,"message_ids":[]},'
        '"payment_request":{"score":0.0,"message_ids":[]}}'
    )
    engine = HiveEngine(
        agent_client=fake_client(classifier),
        sandbox_runner=FakeSandbox(),
        ner_backend=FakeNer(),
        enable_early_exit=False,
    )

    replayed = replay_history_record(record, engine)

    assert [(item.kind, item.value, item.source_msg_id) for item in replayed.hvis] == [
        ("person_name", "petasan", 21)
    ]


class FakeHistory:
    def __init__(self):
        self.records = {
            "8514213f-a1eb-4986-a2dc-bd3fa196ea96": {
                "id": "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
                "peer_id": 919,
            },
            "8d195b38-c1c3-4e00-ac1a-bcc5851ea2be": {
                "id": "8d195b38-c1c3-4e00-ac1a-bcc5851ea2be",
                "peer_id": 919,
                "replay_of": "old",
            },
        }

    def get(self, history_id):
        return self.records.get(history_id)

    def list(self):
        return list(self.records.values())


def test_batch_reanalysis_selects_original_cases_only():
    selected = _archived_records(FakeHistory(), "all")

    assert [record["id"] for record in selected] == [
        "8514213f-a1eb-4986-a2dc-bd3fa196ea96"
    ]
