"""Web control panel tests (FastAPI TestClient), fully offline."""

import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hive.state import HVI, Message, Phase, SessionState
from hive.vault.hashchain import HashChain
from hive.webpanel import create_app

TOKEN = "s3cret"


@dataclass
class FakeSettings:
    panel_token: str = TOKEN
    default_persona: str = "confused_elderly"
    signing_key_path: str = "key.pem"


class FakeEngine:
    def __init__(self):
        self.closed = []

    def close_session(self, session, chain, out_path, key_path, operator_name=""):
        self.closed.append(out_path)
        return out_path

    def summary(self, session):
        return "SUMMARY"


class FakeUserbot:
    def __init__(self):
        self._sessions = {}
        self._observed_chats = []

    def list_observed_chats(self):
        return self._observed_chats

    def begin_takeover(self, peer_id, persona):
        s = SessionState(peer_id=peer_id, persona=persona, phase=Phase.ACTIVE)
        self._sessions[peer_id] = (s, HashChain())

    def end_takeover(self, peer_id):
        return self._sessions.pop(peer_id, None)


@pytest.fixture
def client():
    engine, userbot = FakeEngine(), FakeUserbot()
    # seed one active session with some state
    s = SessionState(peer_id=100, persona="confused_elderly", phase=Phase.ACTIVE)
    s.turn_count = 2
    s.verdict = "likely_scam"
    s.verdict_score = 0.81
    s.messages.append(Message("stranger", "transfer to Maybank 123", time.time(), 0))
    s.hvis.append(HVI(kind="bank_account", value="123", source_msg_id=0, confidence=0.9))
    userbot._sessions[100] = (s, HashChain())
    app = create_app(engine, userbot, FakeSettings())
    c = TestClient(app)
    c._engine, c._userbot = engine, userbot
    return c


def _h():
    return {"X-HIVE-Token": TOKEN}


def test_requires_token(client):
    assert client.get("/api/sessions").status_code == 401


def test_index_is_public(client):
    r = client.get("/")
    assert r.status_code == 200 and "HIVE Control Panel" in r.text
    assert '<img src="/logo.png"' in r.text
    assert "background:transparent" in r.text
    assert ".brandmark{width:40px;height:40px;overflow:hidden;display:block;background:#fff" not in r.text


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


def test_session_detail(client):
    r = client.get("/api/sessions/100", headers=_h())
    d = r.json()
    assert d["hvi_items"][0]["value"] == "123"
    assert d["messages"][0]["role"] == "stranger"


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


def test_takeover_rejects_bad_persona(client):
    r = client.post("/api/takeover", headers=_h(), json={"peer_id": 201, "persona": "wizard"})
    assert r.status_code == 400


def test_stop_seals_and_removes(client):
    r = client.post("/api/sessions/100/stop", headers=_h())
    assert r.status_code == 200 and r.json()["summary"] == "SUMMARY"
    assert 100 not in client._userbot._sessions
    assert Path(client._engine.closed[0]).parts[-2:] == ("evidence", "bundle_100.pdf")


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
    assert "params.get('token')||INJECTED_TOKEN||sessionStorage.getItem" in page


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
