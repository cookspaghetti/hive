"""Permanent audit ledger durability, integrity, mirroring, and panel tests."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from hive import audit
from hive.audit import AuditLogHandler, DurableAuditLedger, JsonlAuditStore
from hive.runtime import HiveEngine
from hive.state import Message
from hive.webpanel import create_app
from tests.fakes import fake_client


def test_jsonl_ledger_preserves_exact_unicode_payload_and_hash_chain(tmp_path):
    path = tmp_path / "audit" / "events.jsonl"
    ledger = DurableAuditLedger(path)

    first = ledger.append(
        "message",
        "inbound_recorded",
        component="test",
        payload={"text": "你好，转账 RM 88.50", "nested": {"reply": "可以啊"}},
        peer_id=42,
        session_id="session-a",
        ts=10.5,
    )
    second = ledger.append(
        "reply_delivery",
        "telegram_send_succeeded",
        component="test",
        payload={"text": "等一下 ya"},
        peer_id=42,
        session_id="session-a",
        ts=11.5,
    )

    assert first["sequence"] == 1
    assert second["sequence"] == 2
    assert second["prev_hash"] == first["event_hash"]
    assert ledger.status()["valid"] is True
    assert ledger.list(peer_id=42)[0]["payload"]["text"] == "你好，转账 RM 88.50"
    assert "你好，转账 RM 88.50" in path.read_text(encoding="utf-8")


def test_ledger_can_filter_multiple_event_types_before_applying_limit(tmp_path):
    ledger = DurableAuditLedger(tmp_path / "events.jsonl")
    ledger.append("takeover", "takeover_started", component="test")
    for index in range(20):
        ledger.append(
            "panel_request",
            "http_request_completed",
            component="test",
            payload={"index": index},
        )
    ledger.append("verdict", "verdict_updated", component="test")

    rows = ledger.list(limit=2, event_types={"takeover", "verdict"})

    assert [row["event_type"] for row in rows] == ["takeover", "verdict"]


def test_ledger_refuses_to_continue_after_tampering(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = DurableAuditLedger(path)
    ledger.append("message", "recorded", component="test", payload={"text": "original"})
    path.write_text(
        path.read_text(encoding="utf-8").replace("original", "altered"),
        encoding="utf-8",
    )

    assert ledger.status()["valid"] is False
    with pytest.raises(RuntimeError, match="integrity check failed"):
        DurableAuditLedger(path)


def test_source_identity_is_recovered_but_a_changed_identity_is_rejected(tmp_path):
    path = tmp_path / "events.jsonl"
    ledger = DurableAuditLedger(path)
    record = ledger.append("runtime", "started", component="test")
    source_path = path.with_suffix(".jsonl.source")
    source_path.unlink()

    recovered = JsonlAuditStore(path)
    assert recovered.source_id == record["source_id"]
    source_path.write_text("different-source", encoding="ascii")
    with pytest.raises(RuntimeError, match="source identity check failed"):
        JsonlAuditStore(path)


def test_postgres_outage_never_drops_local_events_and_resyncs(tmp_path):
    class RecoveringMirror:
        def __init__(self):
            self.fail = True
            self.synced = []

        def write(self, record):
            if self.fail:
                raise OSError("database offline")
            self.synced.append(record)

        def sync(self, records):
            self.fail = False
            self.synced.extend(records)

    ledger = DurableAuditLedger(tmp_path / "events.jsonl")
    mirror = RecoveringMirror()
    ledger.mirror = mirror

    ledger.append("message", "first", component="test")
    assert ledger.status()["events"] == 1
    assert ledger.status()["postgres_in_sync"] is False
    ledger._next_mirror_retry = 0
    ledger.append("message", "second", component="test")

    assert ledger.status()["events"] == 2
    assert ledger.status()["postgres_in_sync"] is True
    assert [record["action"] for record in mirror.synced] == ["first", "second"]


def test_log_capture_redacts_credentials_but_keeps_operational_detail(monkeypatch):
    class CapturingLedger:
        def __init__(self):
            self.rows = []

        def append(self, *args, **kwargs):
            self.rows.append((args, kwargs))
            return {}

    ledger = CapturingLedger()
    monkeypatch.setattr(audit, "_LEDGER", ledger)
    record = logging.LogRecord(
        "hive.worker",
        logging.INFO,
        __file__,
        12,
        "request finished api_key=supersecret password=hunter2 peer=45",
        (),
        None,
    )

    AuditLogHandler().emit(record)

    payload = ledger.rows[0][1]["payload"]
    assert "peer=45" in payload["message"]
    assert "supersecret" not in payload["message"]
    assert "hunter2" not in payload["message"]
    assert payload["message"].count("[redacted]") == 2


def test_authenticated_panel_exposes_raw_ledger_and_integrity_status(tmp_path):
    class Runtime:
        engine = None
        userbot = None
        settings = None
        is_running = False
        restart_required = False

        def mark_restart_required(self):
            self.restart_required = True

        def snapshot(self):
            return {
                "state": "stopped",
                "running": False,
                "ready": False,
                "checks": {},
                "components": {},
                "active_sessions": 0,
                "error": "",
            }

    ledger = DurableAuditLedger(tmp_path / "events.jsonl")
    ledger.append(
        "message",
        "inbound_recorded",
        component="transport.userbot",
        payload={"text": "exact message"},
        peer_id=991,
    )
    app = create_app(
        runtime_manager=Runtime(),
        root=tmp_path,
        session_token="audit-token",
        audit_ledger=ledger,
    )
    client = TestClient(app)

    assert client.get("/api/audit").status_code == 401
    headers = {"X-HIVE-Token": "audit-token"}
    response = client.get("/api/audit?peer_id=991", headers=headers)
    assert response.status_code == 200
    assert response.json()["items"][0]["payload"]["text"] == "exact message"
    status = client.get("/api/audit/status", headers=headers).json()
    assert status["valid"] is True
    assert status["events"] >= 1


def test_panel_activity_defaults_to_curated_milestones_with_full_ledger_option(tmp_path):
    class Runtime:
        engine = None
        userbot = None
        settings = None
        is_running = False
        restart_required = False

        def mark_restart_required(self):
            self.restart_required = True

        def snapshot(self):
            return {
                "state": "stopped",
                "running": False,
                "ready": False,
                "checks": {},
                "components": {},
                "active_sessions": 0,
                "error": "",
            }

    ledger = DurableAuditLedger(tmp_path / "events.jsonl")
    ledger.append("panel_request", "http_request_completed", component="webpanel.http")
    ledger.append(
        "takeover_request",
        "takeover_request_created",
        component="transport.userbot",
        payload={"name": "Alice", "text": "hello"},
        peer_id=77,
    )
    for score in (0.81, 0.86):
        ledger.append(
            "verdict",
            "verdict_updated",
            component="orchestrator.verdict",
            payload={"verdict": "likely_scam", "score": score},
            peer_id=77,
            session_id="case-a",
        )
    ledger.append(
        "extraction",
        "indicators_extracted",
        component="orchestrator.extraction",
        payload={"discovered": []},
        peer_id=77,
    )
    ledger.append(
        "reply_delivery",
        "telegram_send_succeeded",
        component="transport.userbot",
        payload={"text": "routine reply"},
        peer_id=77,
    )
    app = create_app(
        runtime_manager=Runtime(),
        root=tmp_path,
        session_token="audit-token",
        audit_ledger=ledger,
    )
    client = TestClient(app)
    headers = {"X-HIVE-Token": "audit-token"}

    curated = client.get("/api/activity", headers=headers).json()
    all_events = client.get("/api/activity?scope=all", headers=headers).json()

    assert curated["scope"] == "important"
    assert [item["title"] for item in curated["items"]] == [
        "Takeover request created",
        "Verdict updated",
    ]
    assert curated["items"][0]["detail"] == "Alice: hello"
    assert curated["items"][1]["detail"] == "Likely Scam · 81% risk"
    all_titles = {item["title"] for item in all_events["items"]}
    assert "Http request completed" in all_titles
    assert "Telegram send succeeded" in all_titles
    assert client.get("/api/activity?scope=invalid", headers=headers).status_code == 400


def test_real_turn_emits_correlated_message_model_context_and_reply_events(monkeypatch):
    class CapturingLedger:
        def __init__(self):
            self.rows = []

        def append(self, event_type, action, **kwargs):
            row = {"event_type": event_type, "action": action, **kwargs}
            self.rows.append(row)
            return row

    class CleanRunner:
        def run(self, url):
            raise AssertionError(f"unexpected sandbox run: {url}")

    ledger = CapturingLedger()
    monkeypatch.setattr(audit, "_LEDGER", ledger)
    engine = HiveEngine(
        agent_client=fake_client("ok can, what happened?"),
        sandbox_runner=CleanRunner(),
        enable_early_exit=False,
    )
    session, chain = engine.new_session(818, "confused_elderly")

    engine.process_turn(session, chain, Message("stranger", "hello there", 100.0, 1))

    actions = {row["action"] for row in ledger.rows}
    assert {
        "inbound_recorded",
        "chat_completion_requested",
        "chat_completion_received",
        "current_session_facts_prepared",
        "reply_plan_created",
        "outbound_recorded",
    } <= actions
    model_events = [row for row in ledger.rows if row["event_type"].startswith("llm_")]
    assert model_events
    assert all(row["peer_id"] == 818 for row in model_events)
    assert all(row["session_id"] == session.session_id for row in model_events)
