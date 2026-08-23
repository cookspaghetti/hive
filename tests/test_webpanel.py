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


class FakeUserbot:
    def __init__(self):
        self._sessions = {}
        self._observed_chats = []
        self._paused_recoveries = set()

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
    assert '<img src="/logo.png"' in r.text
    assert '<link rel="stylesheet" href="/panel.css?v=' in r.text
    assert '<script src="/panel.js?v=' in r.text
    assert "__ASSET_VERSION__" not in r.text
    assert "Operations overview" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_panel_assets_are_served(client):
    css = client.get("/panel.css")
    script = client.get("/panel.js")
    page = client.get("/")

    assert css.status_code == 200 and "--accent:" in css.text
    assert script.status_code == 200 and 'api("/api/dashboard")' in script.text
    assert ".live-update > span:first-child" in css.text
    assert ".live-update > span {" not in css.text
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in css.text
    assert "height: clamp(650px, 72vh, 780px)" in css.text
    assert 'id="demoMode"' in page.text
    assert 'id="interactiveDemoForm"' in page.text
    assert '$("#interactiveDemoForm").addEventListener("submit"' in script.text
    assert "/messages`" in script.text
    assert 'controlDemo(interactive ? "finish" : "stop")' in script.text
    assert 'id="activityScope"' in page.text
    assert 'data-page="retention"' in page.text
    assert 'id="retentionForm"' in page.text
    assert 'id="signingKeyDetails"' in page.text
    assert 'id="rotateSigningKey"' in page.text
    assert 'api("/api/setup/signing-key/rotate"' in script.text
    assert 'api("/api/retention")' in script.text
    assert 'api("/api/retention/policy"' in script.text
    assert "No artifacts were deleted" in script.text
    assert "All audit events" in page.text
    assert "scope=${encodeURIComponent(scope)}" in script.text
    assert '$("#activityScope").addEventListener("change"' in script.text
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
    assert 'fetch("/api/panel/session", { cache: "no-store" })' in script


def test_frontend_refreshes_a_rotated_ephemeral_session_and_retries_once(client):
    script = client.get("/panel.js").text

    assert "let tokenRefresh = null" in script
    assert "response.status === 401 && retryAuthentication" in script
    assert "await ensurePanelToken({ force: true })" in script
    assert "return api(path, options, false)" in script


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


def test_session_detail_includes_guided_reporting(client):
    response = client.get("/api/sessions/100", headers=_h())

    assert response.status_code == 200
    guidance = response.json()["reporting_guidance"]
    assert guidance["automated_submission"] is False
    assert "997" in guidance["steps"][0]["action"]
    assert 'data-inspector-tab="reporting"' in client.get("/").text


def test_dashboard_aggregates_live_operations(client):
    response = client.get("/api/dashboard", headers=_h())

    assert response.status_code == 200
    data = response.json()
    assert data["metrics"]["active_sessions"] == 1
    assert data["metrics"]["likely_scams"] == 1
    assert data["metrics"]["hvis"] == 1
    assert data["sessions"][0]["peer_id"] == 100


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


def test_session_detail(client):
    r = client.get("/api/sessions/100", headers=_h())
    d = r.json()
    assert d["hvi_items"][0]["value"] == "123"
    assert d["hvi_items"][0]["source_msg_id"] == 0
    assert d["messages"][0]["role"] == "stranger"
    assert d["media_analysis"] == []
    assert d["scam_vector"]["schema_version"] == 2
    assert "transfer to Maybank" in d["scam_vector"]["redacted_script"]
    assert d["case_intelligence"]["privacy_mode"] == "identifier_redacted"
    assert "embedding_text" not in d


def test_session_detail_exposes_messages_added_after_initial_request(client):
    initial = client.get("/api/sessions/100", headers=_h()).json()
    client._userbot._sessions[100][0].messages.append(
        Message("agent", "Which account should I use?", time.time(), 2)
    )

    updated = client.get("/api/sessions/100", headers=_h()).json()

    assert len(updated["messages"]) == len(initial["messages"]) + 1
    assert updated["messages"][-1]["text"] == "Which account should I use?"


def test_panel_script_polls_the_selected_session_for_live_updates(client):
    script = client.get("/panel.js").text

    assert "const LIVE_SESSION_REFRESH_MS = 2000" in script
    assert "async function refreshSelectedSession()" in script
    assert "refreshSelectedSession().catch(() => {})" in script


def test_intelligence_workspace_can_open_archived_runs(client):
    page = client.get("/").text
    script = client.get("/panel.js").text

    assert "Case Intelligence" in page
    assert "Scam-pattern profiles, evidence-backed relationships" in page
    assert '<optgroup label="Active sessions">' in script
    assert '<optgroup label="Previous runs">' in script
    assert "async function openIntelligenceHistory(historyId)" in script
    assert "async function openHistory(historyId)" in script
    assert script.count('analyses.find((item) => item.kind === "reanalysis")') == 2
    assert "openIntelligenceHistory(identifier)" in script
    assert 'id="intelligenceAnalysis"' in page
    assert 'id="reanalyzeHistory"' in page
    assert "async function selectIntelligenceAnalysis(runId)" in script
    assert "async function reanalyzeHistory()" in script
    assert "/analyses/${encodeURIComponent(selected.id)}" in script
    assert "/reanalyze`" in script
    assert "Latest reanalysis" in script
    assert "Schema v${selectedAnalysis.schema_version}" in script
    assert 'archived ? "Archived transcript" : "Active transcript"' in script
    assert "function renderSandboxResult(item)" in script
    assert "function renderMediaAnalysis(item)" in script
    assert "function renderRelatedCase(item, currentVector = {})" in script
    assert "function renderRelationshipGraph(items, peerId)" in script
    assert "function renderPatternProfile(session)" in script
    assert "function comparePatternProfiles(current = {}, related = {})" in script
    assert 'id="patternProfile"' in page
    assert "Scam Pattern Profile" in page
    assert 'id="mediaAnalysisList"' in page
    assert 'id="relatedCasesList"' in page
    assert 'id="relationshipGraph"' in page
    assert "Local QR/OCR and fallback vision findings" in page
    assert "Verified evidence links remain distinct" in page
    assert "/related`" in script
    assert "Candidate similarity" in script
    assert "Verified identifier link" in script
    assert "Case relationship graph" in script
    assert "A sandbox run starts when a URL or bare domain is found" in script


def test_takeover_inspector_is_a_live_media_aware_dialog(client):
    page = client.get("/").text
    css = client.get("/panel.css").text
    script = client.get("/panel.js").text

    assert '<dialog class="session-dialog" id="sessionInspector"' in page
    assert 'id="inspectorEmpty"' not in page
    assert "dialog.showModal()" in script
    assert "renderTranscriptMessage" in script
    assert "transcript-image" in script
    assert "renderSignal(signal, session.messages)" in script
    assert "resolveSignalMessages" in script
    assert "Related messages" in script
    assert "Session risk" in script
    assert "Current assessment" in script
    assert "Carried session evidence" in script
    assert "source_msg_id" in script
    assert "JSON.stringify(signal)" not in script
    assert "Each assessment links its risk signals" in page
    assert ".signal-message" in css
    assert "[hidden] { display: none !important; }" in css
    assert ".session-dialog #inspectorContent:not([hidden])" in css
    assert ".inspector-panel.active { display: flex; flex-direction: column; }" in css
    assert "body:has(.session-dialog[open]) { overflow: hidden; }" in css


def test_panel_wraps_long_audit_details_and_sandbox_urls(client):
    css = client.get("/panel.css").text

    assert ".surface-heading > div { min-width: 0; }" in css
    assert ".surface-heading strong, .surface-heading p { overflow-wrap: anywhere; }" in css
    assert ".timeline-item > div { min-width: 0; }" in css
    assert ".timeline-item p { margin: 2px 0 0; overflow-wrap: anywhere;" in css


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

    assert "function notifyTakeoverRequests(chats)" in script
    assert "async function pollTakeoverRequests()" in script
    assert "New takeover request from" in script
    assert "seenTakeoverRequests: new Set()" in script


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
    assert (client._root / "evidence" / "cases" / f"{history[0]['id']}.json").is_file()


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
    downloaded = client.get(row["package_download_url"], headers=_h())
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK-test"
    assert client.get("/api/evidence-packages/../secret", headers=_h()).status_code == 404


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
    assert row["package_available"] is True
    detail = client.get(f"/api/evaluations/{row['id']}", headers=_h())
    assert detail.status_code == 200
    assert detail.json()["transcript"][1][1] == "Which account?"
    assert "_package_path" not in detail.json()
    downloaded = client.get(row["package_download_url"], headers=_h())
    assert downloaded.status_code == 200
    assert downloaded.content == b"PK-evaluation"
    assert client.get("/api/evaluations/not-a-run", headers=_h()).status_code == 404


def test_evaluation_page_is_available_in_panel(client):
    page = client.get("/").text
    script = client.get("/panel.js").text

    assert 'data-route="evaluation"' in page
    assert 'id="evaluationRows"' in page
    assert 'api("/api/evaluations")' in script


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
