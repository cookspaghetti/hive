"""Immutable analysis-run provenance and persistence tests."""

from uuid import UUID

import pytest

from hive.analysis_runs import (
    LocalAnalysisRunStore,
    analysis_run_record,
    model_manifest,
    transcript_sha256,
)
from hive.state import HVI, Message, SessionState


class Settings:
    llm_model_cheap = "cheap-model"
    llm_model_strong = "strong-model"
    llm_model_light = "classifier-model"
    vision_model = "vision-model"
    llm_api_key = "must-not-leak"


def _history() -> dict:
    return {
        "id": "8514213f-a1eb-4986-a2dc-bd3fa196ea96",
        "peer_id": 919,
        "messages": [
            {
                "role": "stranger",
                "text": "pay this account",
                "ts": 10,
                "msg_id": 11,
                "media_sha256": "abc",
            }
        ],
    }


def test_transcript_digest_is_stable_and_content_sensitive():
    messages = _history()["messages"]

    assert transcript_sha256(messages) == transcript_sha256([dict(messages[0])])
    changed = [dict(messages[0], text="different")]
    assert transcript_sha256(messages) != transcript_sha256(changed)


def test_model_manifest_excludes_credentials():
    manifest = model_manifest(Settings())

    assert manifest == {
        "agent_cheap": "cheap-model",
        "agent_strong": "strong-model",
        "behavior_classifier": "classifier-model",
        "vision": "vision-model",
    }
    assert "must-not-leak" not in str(manifest)


def test_local_analysis_runs_are_immutable_and_case_scoped(tmp_path):
    history = _history()
    session = SessionState(peer_id=919, persona="confused_elderly")
    session.messages.append(Message("stranger", "pay this account", 10, 11))
    session.hvis.append(HVI("bank_account", "12345678", 11, 0.8, "regex"))
    session.verdict = "likely_scam"
    session.verdict_score = 0.83
    run = analysis_run_record(history, session, models=model_manifest(Settings()), created_ts=20)
    store = LocalAnalysisRunStore(tmp_path)

    stored = store.create(run)

    assert UUID(stored["id"]).version == 4
    assert stored["history_id"] == history["id"]
    assert stored["transcript_sha256"] == transcript_sha256(history["messages"])
    assert store.list(history["id"])[0]["id"] == stored["id"]
    assert store.get(history["id"], stored["id"]) == stored
    assert store.get("1787156156531010074_919", stored["id"]) is None
    with pytest.raises(ValueError, match="already exists"):
        store.create(run)
