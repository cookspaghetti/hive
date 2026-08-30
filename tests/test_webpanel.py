"""Web control panel tests (FastAPI TestClient), fully offline."""

import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.analysis_runs import analysis_run_record
from hive.state import HVI, Message, Phase, SessionState
from hive.vault.hashchain import HashChain
from hive.vault.package import build_evidence_package
from hive.vault.signer import generate_keypair, sign_bytes
from hive.webpanel import create_app

TOKEN = "s3cret"


@dataclass
class FakeSettings:
    panel_token: str = TOKEN
    default_persona: str = "confused_elderly"
    signing_key_path: str = "key.pem"
    llm_base_url: str = "https://example.test/v1"
    llm_api_key: str = "test-key"
    llm_model_cheap: str = "cheap-model"
    llm_model_strong: str = "strong-model"
    llm_model_light: str = "light-model"
    vision_model: str = "vision-model"
    tg_phone: str = "+60123456789"
    tg_api_id: int = 123
    tg_api_hash: str = "hash"
    control_bot_token: str = "bot-token"
    operator_id: int = 456
    operator_name: str = "Test Operator"


class FakeEngine:
    def __init__(self):
        self.closed = []
        self.fail_seal = False

    def close_session(self, session, chain, out_path, key_path, operator_name=""):
        if self.fail_seal:
            raise RuntimeError("report renderer failed")
        self.closed.append(out_path)
        return out_path

    def summary(self, session):
        return "SUMMARY"

    def enrich_threat_intelligence(
        self, session, _indicators, _messages, *, force=False
    ):
        assert force is True
        observation = {
            "provider": "semak_mule",
            "provider_label": "Semak Mule",
            "indicator_kind": "bank_account",
            "observable": "123",
            "source_msg_id": 0,
            "status": "no_hit",
            "risk": "clear",
            "summary": "No matching public report was returned.",
            "checked_ts": time.time(),
        }
        session.threat_intelligence = [observation]
        return [observation]


class FakeUserbot:
    def __init__(self):
        self._sessions = {}
        self._observed_chats = []
        self._paused_recoveries = set()
        self._processing_peers = set()

    def list_observed_chats(self):
        return self._observed_chats

    def begin_takeover(self, peer_id, persona):
        s = SessionState(peer_id=peer_id, persona=persona, phase=Phase.ACTIVE)
        self._sessions[peer_id] = (s, HashChain())

    async def process_pending_takeover(self, peer_id):
        return 0

    def end_takeover(self, peer_id):
        return self._sessions.pop(peer_id, None)

    def recovery_status(self, peer_id):
        return "paused_after_restart" if peer_id in self._paused_recoveries else "active"

    def is_processing(self, peer_id):
        return peer_id in self._processing_peers

    async def resume_recovery(self, peer_id):
        if peer_id not in self._sessions:
            raise LookupError
        if peer_id not in self._paused_recoveries:
            raise ValueError
        self._paused_recoveries.remove(peer_id)
        return 2

    def abandon_recovery(self, peer_id):
        if peer_id not in self._paused_recoveries:
            raise ValueError
        self._paused_recoveries.remove(peer_id)
        return self.end_takeover(peer_id)

    def update_persona(self, peer_id, persona):
        self._sessions[peer_id][0].persona = persona

    def dismiss_takeover_request(self, peer_id):
        for chat in self._observed_chats:
            if chat.get("peer_id") == peer_id and chat.get("request_pending"):
                chat["request_pending"] = False
                return True
        return False

    def checkpoint_takeover(self, peer_id):
        self.last_checkpointed_peer = peer_id


def fake_reanalysis(record, engine, settings):
    session = SessionState(peer_id=int(record["peer_id"]), persona=record["persona"])
    session.turn_count = 1
    session.exchange_count = 1
    session.verdict = "likely_scam"
    session.verdict_score = 0.91
    session.hvis.append(HVI("bank_account", "87654321", 0, 0.8, "regex"))
    return analysis_run_record(
        record,
        session,
        models={"behavior_classifier": settings.llm_model_light},
        created_ts=50,
    )


@pytest.fixture
def client(tmp_path):
    engine, userbot = FakeEngine(), FakeUserbot()
    # seed one active session with some state
    s = SessionState(peer_id=100, persona="confused_elderly", phase=Phase.ACTIVE)
    s.turn_count = 2
    s.verdict = "likely_scam"
    s.verdict_score = 0.81
    s.messages.append(Message("stranger", "transfer to Maybank 123", time.time(), 0))
    s.hvis.append(HVI(kind="bank_account", value="123", source_msg_id=0, confidence=0.9))
    userbot._sessions[100] = (s, HashChain())
    app = create_app(
        engine,
        userbot,
        FakeSettings(),
        root=tmp_path,
        reanalysis_runner=fake_reanalysis,
    )
    c = TestClient(app)
    c._engine, c._userbot, c._root = engine, userbot, tmp_path
    return c


def _h():
    return {"X-HIVE-Token": TOKEN}


def test_requires_token(client):
    assert client.get("/api/sessions").status_code == 401


def test_index_is_public(client):
    r = client.get("/")
    assert r.status_code == 200 and "HIVE Control Panel" in r.text
    assert 'rel="icon" type="image/png" href="/favicon.png"' in r.text
    assert '<div id="root">' in r.text
    assert '<link rel="stylesheet" href="/panel.css?v=' in r.text
    assert '<script src="/panel.js?v=' in r.text
    assert "__ASSET_VERSION__" not in r.text
    assert "Operations overview" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_panel_assets_are_served(client):
    css = client.get("/panel.css")
    script = client.get("/panel.js")
    page = client.get("/")

    assert css.status_code == 200 and "--gold:#d7a348" in css.text
    assert script.status_code == 200 and "/api/dashboard" in script.text
    for endpoint in (
        "/api/sessions",
        "/api/history",
        "/api/evidence",
        "/api/activity?limit=300",
        "/api/logs?limit=400",
        "/api/demo/scenarios",
        "/api/evaluations",
        "/api/setup/status",
        "/api/retention/policy",
    ):
        assert endpoint in script.text
    assert "Scripted scammer" in script.text
    assert "Model-driven scammer" in script.text
    assert "Interactive presenter" in script.text
    assert "Command palette" in script.text
    assert "Evidence vault" in script.text
    assert "Save and restart agent" in script.text
    assert "Semak Mule and RDAP work without keys" in script.text
    assert "New Semak Mule key" not in script.text
    assert "What broke" in script.text
    assert "Open peer" in script.text
    assert "Safe package structure" in script.text
    assert "@media(max-width:680px)" in css.text
    assert "prefers-reduced-motion:reduce" in css.text
    assert '<div id="root">' in page.text
    assert css.headers["cache-control"] == "no-store"
    assert script.headers["cache-control"] == "no-store"


def test_retention_report_is_authorised_and_never_deletes(client):
    media = client._root / "evidence" / "media" / "capture.jpg"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"captured")

    assert client.get("/api/retention").status_code == 401
    response = client.get("/api/retention", headers=_h())

    assert response.status_code == 200
    report = response.json()
    assert report["mode"] == "report_only"
    assert report["enforcement_available"] is False
    assert report["deletion_performed"] is False
    assert report["summary"]["files"] == 1
    assert media.exists()


def test_retention_policy_save_persists_review_thresholds_without_deletion(client):
    response = client.put(
        "/api/retention/policy",
        headers=_h(),
        json={
            "media_days": 14,
            "demo_days": 45,
            "evaluation_days": 120,
            "active_checkpoint_review_days": 3,
        },
    )

    assert response.status_code == 200
    report = response.json()
    assert report["policy"]["media_days"] == 14
    assert report["policy"]["active_checkpoint_review_days"] == 3
    assert report["deletion_performed"] is False
    saved = (client._root / ".env").read_text(encoding="utf-8")
    assert "HIVE_RETENTION_MEDIA_DAYS=14" in saved
    assert "HIVE_RETENTION_ACTIVE_REVIEW_DAYS=3" in saved


def test_retention_policy_rejects_destructive_or_invalid_fields(client):
    destructive = client.put(
        "/api/retention/policy",
        headers=_h(),
        json={"delete_after_days": 1},
    )
    invalid = client.put(
        "/api/retention/policy",
        headers=_h(),
        json={"media_days": 0},
    )

    assert destructive.status_code == 400
    assert invalid.status_code == 400
    assert not (client._root / ".env").exists()


def test_signing_key_rotation_is_blocked_while_agent_is_running(client):
    created = client.post(
        "/api/setup/signing-key",
        headers=_h(),
        json={"path": "./secrets/signing_key.pem"},
    ).json()

    response = client.post(
        "/api/setup/signing-key/rotate",
        headers=_h(),
        json={"confirm_fingerprint": created["signing_key"]["fingerprint"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Stop the agent before rotating the evidence signing key."
    )
    assert (client._root / "secrets" / "signing_key.pem").is_file()
    assert len(list((client._root / "secrets").glob("*.pem"))) == 1
    status = client.get("/api/setup/status", headers=_h()).json()
    assert status["signing_key"]["rotation_allowed"] is False
    assert status["signing_key"]["rotation_blocked_reason"] == (
        "Stop the agent before rotating the evidence signing key."
    )


def test_frontend_can_bootstrap_an_ephemeral_backend_session(client):
    response = client.get("/api/panel/session")
    script = client.get("/panel.js").text

    assert response.status_code == 200
    assert response.json() == {"token": TOKEN}
    assert response.headers["cache-control"] == "no-store"
    assert "/api/panel/session" in script


def test_frontend_refreshes_a_rotated_ephemeral_session_and_retries_once(client):
    script = client.get("/panel.js").text

    assert "/api/panel/session" in script
    assert "sessionStorage" in script
    assert "===401" in script


def test_favicon_is_png(client):
    r = client.get("/favicon.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_list_sessions(client):
    r = client.get("/api/sessions", headers=_h())
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["peer_id"] == 100 and rows[0]["verdict"] == "likely_scam"
    assert rows[0]["processing"] is False


def test_session_detail_includes_guided_reporting(client):
    response = client.get("/api/sessions/100", headers=_h())

    assert response.status_code == 200
    guidance = response.json()["reporting_guidance"]
    assert guidance["automated_submission"] is False
    assert "997" in guidance["steps"][0]["action"]
    assert "Evidence and provenance" in client.get("/panel.js").text


def test_dashboard_aggregates_live_operations(client):
    response = client.get("/api/dashboard", headers=_h())

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["active_sessions"] == 1
    assert data["metrics"]["likely_scams"] == 1
    assert data["metrics"]["hvis"] == 1
    assert data["sessions"][0]["peer_id"] == 100


def test_sync_endpoint_reports_cross_channel_revision(client):
    from hive.webpanel.observability import get_observation_hub

    hub = get_observation_hub()
    before = client.get("/api/sync", headers=_h()).json()
    hub.change("test_mutation", peer_id=321)
    after = client.get("/api/sync", headers=_h()).json()

    assert after["revision"] == before["revision"] + 1
    assert after["topic"] == "test_mutation"
    assert after["peer_id"] == 321


def test_model_status_does_not_expose_api_key(client):
    response = client.get("/api/models/status", headers=_h())

    assert response.status_code == 200
    assert response.json()["configured"] is True
    assert response.json()["endpoint"] == "https://example.test/v1"
    assert "test-key" not in response.text


def test_list_recent_incoming_chats(client):
    client._userbot._observed_chats = [
        {
            "peer_id": 200,
            "name": "Incoming user",
            "username": "incoming",
            "last_message": "hello",
            "last_message_id": 10,
            "last_message_at": 123.0,
            "message_count": 1,
            "active": False,
        }
    ]

    response = client.get("/api/chats", headers=_h())

    assert response.status_code == 200
    assert response.json()[0]["peer_id"] == 200


def test_chat_endpoint_returns_only_pending_takeover_requests(client):
    client._userbot._observed_chats = [
        {"peer_id": 200, "active": False, "request_pending": True},
        {"peer_id": 201, "active": False, "request_pending": False},
        {"peer_id": 202, "active": True, "request_pending": False},
    ]

    response = client.get("/api/chats", headers=_h())

    assert [chat["peer_id"] for chat in response.json()] == [200]


def test_panel_can_dismiss_pending_takeover_request(client):
    client._userbot._observed_chats = [
        {"peer_id": 200, "active": False, "request_pending": True},
    ]

    dismissed = client.post("/api/chats/200/dismiss", headers=_h())

    assert dismissed.status_code == 200
    assert dismissed.json() == {"ok": True, "peer_id": 200, "dismissed": True}
    assert client.get("/api/chats", headers=_h()).json() == []
    assert client.post("/api/chats/200/dismiss", headers=_h()).status_code == 404


def test_session_detail(client):
    client._userbot._processing_peers.add(100)
    r = client.get("/api/sessions/100", headers=_h())
    d = r.json()
    assert d["hvi_items"][0]["value"] == "123"
    assert d["hvi_items"][0]["source_msg_id"] == 0
    assert d["messages"][0]["role"] == "stranger"
    assert d["media_analysis"] == []
    assert d["scam_vector"]["schema_version"] == 2
    assert "transfer to Maybank" in d["scam_vector"]["redacted_script"]
    assert d["case_intelligence"]["privacy_mode"] == "identifier_redacted"
    assert d["processing"] is True
    assert "embedding_text" not in d


def test_live_threat_intelligence_refresh_is_persisted_and_hash_chained(client):
    response = client.post(
        "/api/sessions/100/threat-intelligence/refresh",
        headers=_h(),
    )

    assert response.status_code == 200
    assert response.json()["threat_intelligence"][0]["provider"] == "semak_mule"
    session, chain = client._userbot._sessions[100]
    assert session.threat_intelligence[0]["status"] == "no_hit"
    assert chain.entries[-1].payload["event"] == "threat_intelligence"
    assert chain.verify() is True
    assert client._userbot.last_checkpointed_peer == 100


def test_panel_can_correct_live_indicator_with_provenance(client):
    corrected = client.patch(
        "/api/sessions/100/indicators/0",
        headers=_h(),
        json={
            "kind": "bank_account",
            "value": "12345678",
            "reason": "Checked against the source message",
        },
    )

    assert corrected.status_code == 200
    assert corrected.json()["indicator"]["value"] == "12345678"
    assert corrected.json()["indicator"]["extractor"] == "operator_review"
    session = client._userbot._sessions[100][0]
    assert session.hvis[0].confidence == 1.0
    assert session.indicator_reviews[0]["original"]["value"] == "123"
    assert client._userbot.last_checkpointed_peer == 100
    assert client.get("/api/sessions/100", headers=_h()).json()["hvi_items"][0][
        "value"
    ] == "12345678"


def test_indicator_correction_validates_reason_and_index(client):
    assert (
        client.patch(
            "/api/sessions/100/indicators/0",
            headers=_h(),
            json={"kind": "bank_account", "value": "12345678", "reason": "x"},
        ).status_code
        == 400
    )
    assert (
        client.patch(
            "/api/sessions/100/indicators/99",
            headers=_h(),
            json={"kind": "bank_account", "value": "12345678", "reason": "checked"},
        ).status_code
        == 404
    )


def test_session_detail_exposes_messages_added_after_initial_request(client):
    initial = client.get("/api/sessions/100", headers=_h()).json()
    client._userbot._sessions[100][0].messages.append(
        Message("agent", "Which account should I use?", time.time(), 2)
    )

    updated = client.get("/api/sessions/100", headers=_h()).json()

    assert len(updated["messages"]) == len(initial["messages"]) + 1
    assert updated["messages"][-1]["text"] == "Which account should I use?"


def test_session_summary_marks_pipeline_analysis_as_pending(client):
    session = client._userbot._sessions[100][0]
    session.phase = Phase.PROBING

    listed = client.get("/api/sessions", headers=_h()).json()
    detail = client.get("/api/sessions/100", headers=_h()).json()

    assert listed[0]["analysis_pending"] is True
    assert detail["analysis_pending"] is True


def test_sandbox_captures_are_authenticated_and_never_expose_paths(client):
    capture = client._root / "evidence" / "sandbox" / "capture.png"
    capture.parent.mkdir(parents=True)
    capture.write_bytes(b"fake-png")
    client._userbot._sessions[100][0].sandbox_results = [
        {"url": "https://example.test", "screenshot_path": str(capture)}
    ]

    detail = client.get("/api/sessions/100", headers=_h()).json()
    result = detail["sandbox_results"][0]

    assert result["capture_available"] is True
    assert result["capture_url"] == "/api/sandbox-captures/live/100/0"
    assert "screenshot_path" not in result
    assert client.get(result["capture_url"]).status_code == 401
    downloaded = client.get(result["capture_url"], headers=_h())
    assert downloaded.status_code == 200
    assert downloaded.content == b"fake-png"
    assert downloaded.headers["content-type"].startswith("image/png")


def test_sandbox_capture_rejects_files_outside_capture_root(client):
    outside = client._root / "not-evidence.png"
    outside.write_bytes(b"private")
    client._userbot._sessions[100][0].sandbox_results = [
        {"url": "https://example.test", "screenshot_path": str(outside)}
    ]

    detail = client.get("/api/sessions/100", headers=_h()).json()

    assert detail["sandbox_results"][0]["capture_available"] is False
    assert client.get("/api/sandbox-captures/live/100/0", headers=_h()).status_code == 404


def test_panel_script_polls_the_selected_session_for_live_updates(client):
    script = client.get("/panel.js").text

    assert "/api/sessions/" in script
    assert "Live findings" in script
    assert "Stop & seal" in script


def test_intelligence_workspace_can_open_archived_runs(client):
    script = client.get("/panel.js").text

    assert "/api/history/" in script
    assert "/related" in script
    assert "Related scam vectors" in script
    assert "Semantic similarity results from the Qdrant pattern index" in script
    assert "semantic similarity is retrieval, not proof".lower() in script.lower()
    assert "Signals and assessments" in script
    assert "Extracted indicators" in script
    assert "Sandbox results" in script


def test_takeover_workspace_is_full_page_and_media_aware(client):
    css = client.get("/panel.css").text
    script = client.get("/panel.js").text

    assert "workspace-view" in script
    for tab in ("conversation", "signals", "indicators", "sandbox", "relationships", "evidence"):
        assert tab in script
    assert "media_url" in script
    assert "media_mime" in script
    assert "source_msg_id" in script
    assert ".workspace-grid" in css
    assert ".transcript" in css
    assert ".message img" in css


def test_panel_wraps_long_audit_details_and_sandbox_urls(client):
    css = client.get("/panel.css").text

    assert "overflow-wrap:anywhere" in css
    assert ".timeline" in css
    assert ".record-grid" in css


def test_session_media_is_served_only_with_panel_authentication(client):
    session = client._userbot._sessions[100][0]
    media_dir = client._root / "evidence" / "media" / session.session_id
    media_dir.mkdir(parents=True)
    media_file = media_dir / "42_receipt.jpg"
    media_file.write_bytes(b"fake-jpeg")
    session.messages.append(
        Message(
            "stranger",
            "payment receipt",
            time.time(),
            42,
            media_kind="image",
            media_name="receipt.jpg",
            media_mime="image/jpeg",
            media_size=9,
            media_path=str(media_file),
            media_sha256="abc123",
        )
    )

    detail = client.get("/api/sessions/100", headers=_h()).json()
    media = detail["messages"][-1]
    assert media["media_name"] == "receipt.jpg"
    assert media["media_url"] == f"/api/media/{session.session_id}/42"
    assert "media_path" not in media

    assert client.get(media["media_url"]).status_code == 401
    response = client.get(media["media_url"], headers=_h())
    assert response.status_code == 200
    assert response.content == b"fake-jpeg"
    assert response.headers["content-type"].startswith("image/jpeg")


def test_panel_notifies_for_new_takeover_requests(client):
    script = client.get("/panel.js").text

    assert "/api/chats" in script
    assert "Pending private chats will appear here" in script
    assert "Queue" in script
    assert "Dismiss request" in script


def test_panel_exposes_operator_indicator_review_workflow(client):
    script = client.get("/panel.js").text

    assert "/indicators/" in script
    assert "Record correction" in script
    assert "Operator reviewed" in script
    assert "signed evidence bundle retains the originally extracted value" in script


def test_takeover_and_persona(client):
    assert (
        client.post(
            "/api/takeover", headers=_h(), json={"peer_id": 200, "persona": "overseas_worker"}
        ).status_code
        == 200
    )
    assert 200 in client._userbot._sessions
    r = client.post(
        "/api/sessions/200/persona", headers=_h(), json={"persona": "small_business_owner"}
    )
    assert r.status_code == 200
    assert client._userbot._sessions[200][0].persona == "small_business_owner"


def test_recovery_paused_session_can_be_resumed_or_abandoned(client):
    client._userbot._paused_recoveries.add(100)

    listed = client.get("/api/sessions", headers=_h()).json()
    assert listed[0]["recovery_status"] == "paused_after_restart"
    resumed = client.post("/api/sessions/100/resume", headers=_h())
    assert resumed.status_code == 200
    assert resumed.json()["processed_messages"] == 2
    assert client._userbot.recovery_status(100) == "active"

    client._userbot._paused_recoveries.add(100)
    abandoned = client.post("/api/sessions/100/abandon", headers=_h())
    assert abandoned.status_code == 200
    assert 100 not in client._userbot._sessions


def test_recovery_actions_reject_live_session(client):
    assert client.post("/api/sessions/100/resume", headers=_h()).status_code == 409
    assert client.post("/api/sessions/100/abandon", headers=_h()).status_code == 409


def test_takeover_rejects_bad_persona(client):
    r = client.post("/api/takeover", headers=_h(), json={"peer_id": 201, "persona": "wizard"})
    assert r.status_code == 400


def test_stop_seals_and_removes(client):
    r = client.post("/api/sessions/100/stop", headers=_h())
    assert r.status_code == 200 and r.json()["summary"] == "SUMMARY"
    assert 100 not in client._userbot._sessions
    bundle = Path(client._engine.closed[0])
    assert bundle.parent.name == "evidence"
    assert bundle.name.startswith("bundle_100_") and bundle.suffix == ".pdf"
    assert r.json()["download_url"] == f"/api/evidence/{bundle.name}"

    history = client.get("/api/history", headers=_h()).json()
    assert history[0]["peer_id"] == 100
    assert history[0]["message_count"] == 1

    detail = client.get(f"/api/history/{history[0]['id']}", headers=_h()).json()
    assert detail["messages"][0]["text"] == "transfer to Maybank 123"
    assert detail["operator_name"] == "Test Operator"
    assert (client._root / "evidence" / "cases" / f"{history[0]['id']}.json").is_file()


def test_panel_can_correct_sealed_indicator_without_rewriting_bundle(client):
    sealed = client.post("/api/sessions/100/stop", headers=_h()).json()
    history_id = sealed["history_id"]

    corrected = client.patch(
        f"/api/history/{history_id}/indicators/0",
        headers=_h(),
        json={
            "kind": "bank_account",
            "value": "87654321",
            "reason": "Operator verified the account from the captured transcript",
        },
    )

    assert corrected.status_code == 200
    payload = corrected.json()
    assert payload["indicator"]["value"] == "87654321"
    assert payload["review"]["signed_bundle_modified"] is False
    detail = client.get(f"/api/history/{history_id}", headers=_h()).json()
    assert detail["hvi_items"][0]["value"] == "87654321"
    assert detail["indicator_reviews"][0]["original"]["value"] == "123"
    profile = json.loads(
        (client._root / "evidence" / "cases" / f"{history_id}.json").read_text(
            encoding="utf-8"
        )
    )
    assert profile["indicators"][0]["normalized_value"] == "87654321"


def test_evidence_index_and_package_download(client):
    evidence = client._root / "evidence"
    evidence.mkdir()
    pdf = evidence / "bundle_100_1.pdf"
    signature = evidence / "bundle_100_1.pdf.sig"
    package = evidence / "bundle_100_1.evidence.zip"
    pdf.write_bytes(b"%PDF-test")
    signature.write_bytes(b"signature")
    package.write_bytes(b"PK-test")

    response = client.get("/api/evidence", headers=_h())

    assert response.status_code == 200
    row = response.json()[0]
    assert row["package_present"] is True
    assert row["package_filename"] == package.name
    assert row["package_download_url"] == f"/api/evidence-packages/{package.name}"
    assert row["metadata_url"] == f"/api/evidence-metadata/{pdf.name}"
    downloaded = client.get(row["package_download_url"], headers=_h())
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK-test"
    assert client.get("/api/evidence-packages/../secret", headers=_h()).status_code == 404


def test_evidence_metadata_runs_real_package_and_custody_verification(client):
    evidence = client._root / "evidence"
    evidence.mkdir()
    pdf = evidence / "bundle_100_2.pdf"
    key = client._root / "signing.pem"
    pdf.write_bytes(b"%PDF-verifiable")
    generate_keypair(str(key), bits=2048)
    Path(str(pdf) + ".sig").write_bytes(sign_bytes(pdf.read_bytes(), str(key)))
    build_evidence_package(pdf, key)

    response = client.get(f"/api/evidence-metadata/{pdf.name}", headers=_h())

    assert response.status_code == 200
    payload = response.json()
    assert payload["operator_name"] == "Test Operator"
    assert payload["verification"]["ok"] is True
    assert all(payload["verification"]["checks"].values())
    assert payload["verification"]["manifest"]["signing_key_fingerprint"]
    assert isinstance(payload["custody"], list)


def test_recorded_evaluation_runs_are_inspectable_and_downloadable(client):
    run_directory = client._root / "evaluation" / "results" / "smoke" / "case-one"
    evidence_directory = run_directory / "evidence"
    evidence_directory.mkdir(parents=True)
    package = evidence_directory / "bundle_1_1.evidence.zip"
    package.write_bytes(b"PK-evaluation")
    record = {
        "scenario": "investment_en_bank_link",
        "archetype": "investment",
        "persona": "confused_elderly",
        "language": "English",
        "turns": 3,
        "exchanges": 3,
        "duration_s": 12.5,
        "engagement_duration_s": 44.5,
        "engagement_duration_basis": "runtime plus planned delay",
        "response_latencies_s": [20.0, 24.0],
        "agent_language": "English/Manglish",
        "language_match": True,
        "transcript": [["scammer", "Transfer now"], ["victim", "Which account?"]],
        "hvi_items": [
            {
                "kind": "bank_account",
                "value": "1234567890",
                "confidence": 0.9,
                "source_msg_id": 0,
                "extractor": "regex",
            }
        ],
        "extraction": {"precision": 1.0, "recall": 1.0, "f1": 1.0},
        "verdict": "likely_scam",
        "verdict_score": 0.91,
        "verdict_correct": True,
        "chain_valid": True,
        "evidence_verified": True,
        # Deliberately use Windows separators to cover Linux container reads.
        "evidence_package": package.relative_to(client._root).as_posix().replace(
            "/", "\\"
        ),
    }
    (run_directory / "redteam_runs.json").write_text(
        json.dumps([record]),
        encoding="utf-8",
    )

    response = client.get("/api/evaluations", headers=_h())

    assert response.status_code == 200
    row = response.json()[0]
    assert row["scenario"] == "investment_en_bank_link"
    assert row["f1"] == 1.0
    assert "engagement_duration_s" not in row
    assert row["character_status"] == "not_assessed"
    assert row["character_metrics"]["session_break"] is None
    assert row["mean_response_latency_s"] == 22.0
    assert row["language_match"] is True
    assert row["package_available"] is True
    detail = client.get(f"/api/evaluations/{row['id']}", headers=_h())
    assert detail.status_code == 200
    assert detail.json()["transcript"][1][1] == "Which account?"
    assert detail.json()["f1"] == 1.0
    assert detail.json()["mean_response_latency_s"] == 22.0
    assert detail.json()["hvi_count"] == 1
    assert "engagement_duration_s" not in detail.json()
    assert detail.json()["character_response_turns"][0]["messages"] == ["Which account?"]
    assert detail.json()["current_evidence_verification"]["ok"] is False
    assert "_package_path" not in detail.json()
    downloaded = client.get(row["package_download_url"], headers=_h())
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK-evaluation"
    assert client.get("/api/evaluations/not-a-run", headers=_h()).status_code == 404


def test_evaluation_page_is_available_in_panel(client):
    script = client.get("/panel.js").text

    assert "Evaluation runs" in script
    assert "/api/evaluations" in script
    assert "Synthetic" in script


def test_character_review_is_authenticated_validated_persistent_and_non_destructive(client):
    folder = client._root / "evaluation" / "results" / "character-test"
    folder.mkdir(parents=True)
    source = folder / "redteam_runs.json"
    record = {"persona": "confused_elderly", "target_response_turns": 1,
              "transcript": [["scammer", "You are a bot."], ["victim", "What bot?"]]}
    source.write_text(json.dumps([record]), encoding="utf-8")
    original = source.read_bytes()
    index = client.get("/api/evaluations", headers=_h()).json()[0]
    url = f"/api/evaluations/{index['id']}"
    detail = client.get(url, headers=_h()).json()
    assert detail["character_status"] == "not_assessed"
    assert detail["character_metrics"]["session_break"] is None
    payload = {
        "source_sha256": detail["character_assessment"]["source_sha256"],
        "previous_review_id": None, "reviewer": "Fixture reviewer", "note": "Regression test only",
        "turns": [{"turn": 1, "verdict": "pass", "reason": "Denial is not self-disclosure.",
                   "findings": []}],
    }
    assert client.post(url + "/character-review", json=payload).status_code == 401
    invalid = {**payload, "turns": [{"turn": 1, "verdict": "break", "reason": "Test finding",
                                   "findings": [{"category": "identity_disclosure",
                                                 "quote": "You are a bot.",
                                                 "reason": "Wrong speaker"}]}]}
    assert client.post(url + "/character-review", headers=_h(), json=invalid).status_code == 400
    response = client.post(url + "/character-review", headers=_h(), json=payload)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["character_status"] == "human_reviewed"
    assert data["character_metrics"]["session_break"] is False
    assert source.read_bytes() == original
    assert len(list((client._root / "evidence" / "evaluation_reviews").glob("*/*.json"))) == 1
    persisted = client.get(url, headers=_h()).json()
    assert persisted["character_review"]["reviewer"] == "Fixture reviewer"
    assert client.post(url + "/character-review", headers=_h(), json=payload).status_code == 409
    payload["previous_review_id"] = data["character_review"]["id"]
    payload["turns"][0]["verdict"] = "uncertain"
    second = client.post(url + "/character-review", headers=_h(), json=payload)
    assert second.status_code == 200
    assert second.json()["character_metrics"]["session_break"] is None
    assert len(second.json()["character_review_history"]) == 2
    missing = client.post("/api/evaluations/missing/character-review", headers=_h(), json=payload)
    assert missing.status_code == 404


def test_character_api_preserves_automated_assessment_and_excludes_stale_scores(client):
    from hive.redteam.character import assess_character
    from tests.fakes import fake_client
    folder = client._root / "evaluation" / "results" / "character-auto"
    folder.mkdir(parents=True)
    source = folder / "redteam_runs.json"
    record = {"persona": "confused_elderly", "target_response_turns": 1,
              "transcript": [["scammer", "hi"], ["victim", "hello"]]}
    judge = fake_client(json.dumps({"turns": [{"turn": 1, "verdict": "pass",
                                               "reason": "Consistent greeting", "findings": []}]}))
    record["character_assessment"] = assess_character(
        record["persona"], record["transcript"], 1, judge,
    )
    source.write_text(json.dumps([record]))
    row = client.get("/api/evaluations", headers=_h()).json()[0]
    assert row["character_status"] == "automated"
    assert row["character_metrics"]["eligible"] is True
    record["transcript"][1][1] = "different response"
    source.write_text(json.dumps([record]))
    row = client.get("/api/evaluations", headers=_h()).json()[0]
    assert row["character_status"] == "error"
    assert row["character_metrics"]["session_break"] is None


def test_stop_failure_keeps_takeover_active_and_unarchived(client):
    client._engine.fail_seal = True

    response = client.post("/api/sessions/100/stop", headers=_h())

    assert response.status_code == 500
    assert response.json()["detail"] == "seal failed; takeover remains active and can be retried"
    assert 100 in client._userbot._sessions
    assert client.get("/api/history", headers=_h()).json() == []


def test_repeated_peer_sessions_receive_distinct_bundle_names(client):
    first = client.post("/api/sessions/100/stop", headers=_h()).json()
    client._userbot.begin_takeover(100, "naive_young_adult")
    second = client.post("/api/sessions/100/stop", headers=_h()).json()

    assert Path(first["bundle"]).name != Path(second["bundle"]).name


def test_takeover_history_rejects_unknown_or_invalid_ids(client):
    assert client.get("/api/history/not-a-record", headers=_h()).status_code == 404
    assert client.get("/api/history/..%2F.env", headers=_h()).status_code == 404


def test_archived_case_keeps_original_and_new_analysis_runs(client):
    client.post("/api/sessions/100/stop", headers=_h())
    history_id = client.get("/api/history", headers=_h()).json()[0]["id"]

    original = client.get(f"/api/history/{history_id}/analyses", headers=_h()).json()
    assert len(original) == 1
    assert original[0]["kind"] == "original"

    queued = client.post(f"/api/history/{history_id}/reanalyze", headers=_h())
    assert queued.status_code == 202
    job = queued.json()
    for _attempt in range(100):
        status = client.get(f"/api/reanalysis/{job['id']}", headers=_h()).json()
        if status["status"] in {"completed", "failed"}:
            break
        time.sleep(0.01)

    assert status["status"] == "completed"
    analyses = client.get(f"/api/history/{history_id}/analyses", headers=_h()).json()
    assert [item["kind"] for item in analyses] == ["original", "reanalysis"]
    selected = client.get(
        f"/api/history/{history_id}/analyses/{status['analysis_run_id']}",
        headers=_h(),
    ).json()
    assert selected["selected_analysis"]["id"] == status["analysis_run_id"]
    assert selected["verdict"] == "likely_scam"
    assert selected["score"] == 0.91
    assert selected["hvi_items"][0]["value"] == "87654321"
    case_profile = (
        client._root / "evidence" / "cases" / f"{history_id}.json"
    ).read_text(encoding="utf-8")
    assert status["analysis_run_id"] in case_profile
    assert "87654321" in case_profile


def test_related_case_api_explains_exact_identifier_link(client):
    first_session = client._userbot._sessions[100][0]
    first_session.hvis[0].value = "12345678"
    first = client.post("/api/sessions/100/stop", headers=_h()).json()["history_id"]
    client._userbot.begin_takeover(200, "overseas_worker")
    second_session = client._userbot._sessions[200][0]
    second_session.messages.append(Message("stranger", "pay this account", time.time(), 8))
    second_session.hvis.append(HVI("bank_account", "12345678", 8, 0.8, "regex"))
    second = client.post("/api/sessions/200/stop", headers=_h()).json()["history_id"]

    related = client.get(f"/api/history/{first}/related", headers=_h()).json()

    assert related[0]["related_history_id"] == second
    assert related[0]["relationship"] == "shared_identifier"
    assert related[0]["score"] == 0.95
    assert related[0]["reasons"][0]["value"] == "12345678"
    assert related[0]["reasons"][0]["current_source_message_ids"] == [0]
    assert related[0]["reasons"][0]["related_source_message_ids"] == [8]
    assert related[0]["pattern_profile"]["schema_version"] == 2
    assert "redacted_script" in related[0]["pattern_profile"]
    assert "embedding_text" not in related[0]

    analyses = client.get(f"/api/history/{first}/analyses", headers=_h()).json()
    original = client.get(
        f"/api/history/{first}/analyses/{analyses[0]['id']}", headers=_h()
    ).json()
    assert original["scam_vector"]["indicator_kinds"] == ["bank_account"]


def test_detail_404_when_missing(client):
    assert client.get("/api/sessions/999", headers=_h()).status_code == 404


class FakeManagedRuntime:
    def __init__(self):
        self.state = "stopped"
        self.is_running = False
        self.engine = None
        self.userbot = None
        self.settings = None
        self.marked = False

    def snapshot(self):
        return {
            "state": self.state,
            "running": self.is_running,
            "restart_required": self.marked,
            "active_sessions": 0,
            "error": "",
            "ready": False,
            "checks": {"llm": False},
            "components": {
                "llm": {"state": "pending", "detail": "", "duration_s": None}
            },
        }

    def mark_restart_required(self):
        self.marked = True

    async def start(self):
        self.state = "running"
        self.is_running = True
        return self.snapshot()

    async def stop(self, force=False):
        self.state = "stopped"
        self.is_running = False
        return self.snapshot()

    async def restart(self, force=False):
        self.state = "running"
        self.is_running = True
        return self.snapshot()


def test_unified_panel_stays_available_when_agent_is_stopped(tmp_path):
    runtime = FakeManagedRuntime()
    app = create_app(runtime_manager=runtime, root=tmp_path, session_token=TOKEN)
    client = TestClient(app)

    status = client.get("/api/runtime/status", headers=_h())
    sessions = client.get("/api/sessions", headers=_h())

    assert status.status_code == 200 and status.json()["state"] == "stopped"
    assert sessions.status_code == 409
    assert sessions.json()["detail"]["code"] == "agent_not_running"
    page = client.get("/").text
    assert TOKEN in page and "__SESSION_TOKEN__" not in page
    assert 'window.__HIVE_PANEL_TOKEN__ = "s3cret"' in page
    script = client.get("/panel.js").text
    assert 'sessionStorage.getItem("hive-panel-token")' in script


def test_health_is_public_and_reports_runtime_components(tmp_path):
    runtime = FakeManagedRuntime()
    app = create_app(runtime_manager=runtime, root=tmp_path, session_token=TOKEN)
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "degraded",
        "panel": "up",
        "runtime": {"state": "stopped", "running": False, "ready": False},
        "components": runtime.snapshot()["components"],
    }


def test_setup_change_marks_managed_runtime_for_restart(tmp_path):
    runtime = FakeManagedRuntime()
    app = create_app(runtime_manager=runtime, root=tmp_path, session_token=TOKEN)
    client = TestClient(app)

    response = client.put("/api/setup/config", headers=_h(), json={"HIVE_LLM_API_KEY": "new-key"})

    assert response.status_code == 200
    assert runtime.marked is True


def test_loopback_panel_rejects_unexpected_host_header(tmp_path):
    runtime = FakeManagedRuntime()
    app = create_app(
        runtime_manager=runtime,
        root=tmp_path,
        session_token=TOKEN,
        bound_host="127.0.0.1",
    )
    client = TestClient(app)

    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/", headers={"Host": "localhost:9130"}).status_code == 200
